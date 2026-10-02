"""SQLite persistence for the email offerings system (stdlib ``sqlite3``).

One :class:`Store` wraps a single ``sqlite3`` connection and owns every table
the engine relies on: offerings, buyers, campaigns, the thread → offering map,
contacts (touches between the system and a buyer), replies, processed inbound
message ids, the audit log of actions, a key/value state table and the daily
send counters used for rate capping.

Storage conventions:

* Datetimes are stored as ISO-8601 strings in UTC with microsecond precision
  (``2026-10-01T12:00:00.000000+00:00``) so they compare correctly as text.
  Naive datetimes are assumed to already be UTC.
* List fields (``tags``, ``attachments``, ``recipients``, ``thread_ids``) are
  stored as JSON text.
* Buyer emails are always stored lower-cased, so every lookup by email is
  case-insensitive.
* Records are rebuilt through the dataclasses' ``from_dict`` helpers where they
  exist, so validation in ``email_offerings.models`` applies on the way out too.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from email_offerings.models import (
    ActionKind,
    ActionRecord,
    Buyer,
    BuyerStatus,
    Campaign,
    CampaignStatus,
    Contact,
    ContactKind,
    Offering,
    OfferingStatus,
    ReplyIntent,
)

MEMORY_PATH: str = ":memory:"

#: Columns the buyer CSV import understands (header row required, any order).
CSV_COLUMNS: tuple[str, ...] = ("email", "name", "company", "postal_code", "city_state", "tags")
#: Separator between tags inside the CSV ``tags`` column.
TAG_SEPARATOR: str = "|"

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS offerings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    fob_location TEXT NOT NULL,
    unit TEXT NOT NULL,
    sell_price REAL NOT NULL,
    quantity_available TEXT NOT NULL DEFAULT '',
    units_per_truckload REAL,
    make_offers INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    cost_price REAL,
    suggested_sell_note TEXT NOT NULL DEFAULT '',
    internal_notes TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    attachments TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS buyers (
    email TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    postal_code TEXT NOT NULL DEFAULT '',
    city_state TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    offering_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    recipients TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL DEFAULT '',
    personal_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    thread_ids TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_campaigns_offering ON campaigns(offering_id, status);
CREATE TABLE IF NOT EXISTS thread_map (
    thread_id TEXT PRIMARY KEY,
    offering_id TEXT NOT NULL,
    buyer_email TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offering_id TEXT NOT NULL,
    buyer_email TEXT NOT NULL,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    campaign_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_contacts_lookup ON contacts(offering_id, buyer_email, kind);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offering_id TEXT NOT NULL,
    buyer_email TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_replies_lookup ON replies(offering_id, buyer_email);
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    offering_id TEXT NOT NULL DEFAULT '',
    buyer_email TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_actions_at ON actions(at, id);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_sends (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);
"""


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #


def _to_iso(value: datetime | None) -> str | None:
    """Serialize a datetime as a UTC ISO-8601 string with fixed precision.

    Naive datetimes are assumed to be UTC. The fixed ``microseconds`` precision
    keeps every stored timestamp the same length so text comparison in SQL
    orders them chronologically.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _from_iso(value: str | None) -> datetime | None:
    """Parse a stored ISO-8601 string back into an aware UTC datetime."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _dump_list(values: Iterable[str] | None) -> str:
    """Serialize a list field as JSON text."""
    return json.dumps(list(values or []))


def _load_list(text: str | None) -> list[str]:
    """Deserialize a JSON list field (``None``/empty → ``[]``)."""
    return list(json.loads(text)) if text else []


def _norm_email(email: str) -> str:
    """Normalize an email address the way :class:`Buyer` does (strip + lower)."""
    return email.strip().lower()


def _day_key(day: date) -> str:
    """ISO date string used as the ``daily_sends`` key (datetimes use their date)."""
    if isinstance(day, datetime):
        day = day.date()
    return day.isoformat()


def _enum_value(value: Any) -> str:
    """Return the ``.value`` of an enum member, or the string itself."""
    return value.value if hasattr(value, "value") else str(value)


class _LineTracker:
    """Iterate over a text file while remembering the raw lines of the current CSV record.

    ``csv.reader`` pulls as many physical lines as a record needs (quoted
    fields may span lines); :meth:`take` returns those lines joined together
    and resets, so a rejected record can be reported verbatim.
    """

    def __init__(self, lines: Iterable[str]) -> None:
        self._lines: Iterator[str] = iter(lines)
        self._current: list[str] = []

    def __iter__(self) -> "_LineTracker":
        return self

    def __next__(self) -> str:
        line = next(self._lines)
        self._current.append(line.rstrip("\r\n"))
        return line

    def take(self) -> str:
        """Return the raw text of the record just read and start a new one."""
        text = "\n".join(self._current)
        self._current = []
        return text


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


class Store:
    """SQLite-backed persistence for offerings, buyers, campaigns and the audit log.

    The constructor opens the database and calls :meth:`initialize`, so callers
    may skip the explicit call. ``path`` defaults to an in-memory database;
    a ``str`` or :class:`pathlib.Path` creates (or opens) a file, including any
    missing parent directories. The store is a context manager that closes
    its connection on exit.
    """

    def __init__(self, path: str | os.PathLike[str] = MEMORY_PATH) -> None:
        self.path: str = os.fspath(path)
        if self.path != MEMORY_PATH:
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self.initialize()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection.

        Raises:
            sqlite3.ProgrammingError: If the store has been closed.
        """
        if self._conn is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed Store.")
        return self._conn

    def initialize(self) -> None:
        """Create every table and index if missing. Safe to call repeatedly."""
        self.connection.executescript(_SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        """Close the connection. Calling it twice is harmless."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def _write(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        cursor = self.connection.execute(sql, params)
        self.connection.commit()
        return cursor

    # ------------------------------------------------------------------ #
    # Offerings
    # ------------------------------------------------------------------ #

    @staticmethod
    def _offering_from_row(row: sqlite3.Row) -> Offering:
        data = dict(row)
        data["make_offers"] = bool(data["make_offers"])
        data["tags"] = _load_list(data["tags"])
        data["attachments"] = _load_list(data["attachments"])
        return Offering.from_dict(data)

    def upsert_offering(self, offering: Offering) -> None:
        """Insert or fully replace an offering keyed by ``offering.id``."""
        data = offering.to_dict()
        self._write(
            """
            INSERT OR REPLACE INTO offerings (
                id, title, description, fob_location, unit, sell_price,
                quantity_available, units_per_truckload, make_offers, status,
                created_at, expires_at, cost_price, suggested_sell_note,
                internal_notes, tags, attachments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data["title"],
                data["description"],
                data["fob_location"],
                data["unit"],
                float(data["sell_price"]),
                data["quantity_available"],
                data["units_per_truckload"],
                1 if data["make_offers"] else 0,
                data["status"],
                _to_iso(offering.created_at),
                _to_iso(offering.expires_at),
                data["cost_price"],
                data["suggested_sell_note"],
                data["internal_notes"],
                _dump_list(data["tags"]),
                _dump_list(data["attachments"]),
            ),
        )

    def get_offering(self, offering_id: str) -> Offering | None:
        """Return the offering with ``offering_id`` or ``None``."""
        row = self._execute("SELECT * FROM offerings WHERE id = ?", (offering_id,)).fetchone()
        return self._offering_from_row(row) if row else None

    def list_offerings(self, status: OfferingStatus | None = None) -> list[Offering]:
        """List offerings (optionally only those with ``status``), oldest first."""
        if status is None:
            rows = self._execute("SELECT * FROM offerings ORDER BY created_at, id").fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM offerings WHERE status = ? ORDER BY created_at, id",
                (_enum_value(status),),
            ).fetchall()
        return [self._offering_from_row(r) for r in rows]

    def set_offering_status(self, offering_id: str, status: OfferingStatus) -> None:
        """Change an offering's status.

        Raises:
            KeyError: If no offering has ``offering_id``.
        """
        cursor = self._write(
            "UPDATE offerings SET status = ? WHERE id = ?", (_enum_value(status), offering_id)
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown offering: {offering_id!r}")

    # ------------------------------------------------------------------ #
    # Buyers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _buyer_from_row(row: sqlite3.Row) -> Buyer:
        data = dict(row)
        data["tags"] = _load_list(data["tags"])
        return Buyer.from_dict(data)

    def upsert_buyer(self, buyer: Buyer) -> None:
        """Insert or fully replace a buyer keyed by its (lower-cased) email."""
        data = buyer.to_dict()
        self._write(
            """
            INSERT OR REPLACE INTO buyers (
                email, name, company, postal_code, city_state, status, tags, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["email"],
                data["name"],
                data["company"],
                data["postal_code"],
                data["city_state"],
                data["status"],
                _dump_list(data["tags"]),
                data["notes"],
                _to_iso(buyer.created_at),
            ),
        )

    def get_buyer(self, email: str) -> Buyer | None:
        """Return the buyer with ``email`` (case-insensitive) or ``None``."""
        row = self._execute("SELECT * FROM buyers WHERE email = ?", (_norm_email(email),)).fetchone()
        return self._buyer_from_row(row) if row else None

    def list_buyers(
        self,
        status: BuyerStatus | None = BuyerStatus.ACTIVE,
        tag: str | None = None,
    ) -> list[Buyer]:
        """List buyers ordered by email.

        Args:
            status: Only buyers with this status; ``None`` returns every buyer.
                Defaults to ``ACTIVE`` because that is the send list.
            tag: Only buyers carrying this tag (exact match).
        """
        if status is None:
            rows = self._execute("SELECT * FROM buyers ORDER BY email").fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM buyers WHERE status = ? ORDER BY email", (_enum_value(status),)
            ).fetchall()
        buyers = [self._buyer_from_row(r) for r in rows]
        if tag is not None:
            buyers = [b for b in buyers if tag in b.tags]
        return buyers

    def set_buyer_status(self, email: str, status: BuyerStatus) -> None:
        """Change a buyer's status (email lookup is case-insensitive).

        Raises:
            KeyError: If no buyer has ``email``.
        """
        cursor = self._write(
            "UPDATE buyers SET status = ? WHERE email = ?", (_enum_value(status), _norm_email(email))
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown buyer: {email!r}")

    def import_buyers_csv(self, path: str | os.PathLike[str]) -> tuple[int, list[str]]:
        """Import buyers from a CSV file with a header row.

        The header must contain ``email``; the other recognised columns are
        ``name``, ``company``, ``postal_code``, ``city_state`` and ``tags``
        (tags separated by ``|``). Column order is free, header names are
        case-insensitive and unknown columns are ignored. Blank lines are
        skipped. Lines whose email is invalid are returned verbatim in the
        rejected list.

        Existing buyers keep their status, notes and creation time; a blank
        CSV field never overwrites an existing non-blank value, and tags are
        merged (existing first, new ones appended).

        Returns:
            ``(imported, rejected_lines)`` where ``imported`` counts rows that
            were inserted or updated.

        Raises:
            ValueError: If the file is empty or its header has no ``email`` column.
        """
        imported = 0
        rejected: list[str] = []
        header: list[str] | None = None
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            tracker = _LineTracker(fh)
            for row in csv.reader(tracker):
                raw = tracker.take()
                if header is None:
                    if not any(cell.strip() for cell in row):
                        continue  # leading blank line
                    header = [cell.strip().lower() for cell in row]
                    if "email" not in header:
                        raise ValueError(
                            "CSV header row required, e.g. " + ",".join(CSV_COLUMNS)
                        )
                    continue
                if not any(cell.strip() for cell in row):
                    continue
                fields = {
                    name: (row[i].strip() if i < len(row) else "") for i, name in enumerate(header)
                }
                try:
                    self._import_buyer_fields(fields)
                except ValueError:
                    rejected.append(raw)
                    continue
                imported += 1
        if header is None:
            raise ValueError("CSV file is empty; a header row is required.")
        return imported, rejected

    def _import_buyer_fields(self, fields: Mapping[str, str]) -> None:
        """Upsert one CSV row, merging into an existing buyer without clobbering data."""
        tags = [t.strip() for t in fields.get("tags", "").split(TAG_SEPARATOR) if t.strip()]
        incoming = Buyer(
            email=fields.get("email", ""),
            name=fields.get("name", ""),
            company=fields.get("company", ""),
            postal_code=fields.get("postal_code", ""),
            city_state=fields.get("city_state", ""),
            tags=tags,
        )
        existing = self.get_buyer(incoming.email)
        if existing is None:
            self.upsert_buyer(incoming)
            return
        merged = Buyer(
            email=existing.email,
            name=incoming.name or existing.name,
            company=incoming.company or existing.company,
            postal_code=incoming.postal_code or existing.postal_code,
            city_state=incoming.city_state or existing.city_state,
            status=existing.status,
            tags=existing.tags + [t for t in incoming.tags if t not in existing.tags],
            notes=existing.notes,
            created_at=existing.created_at,
        )
        self.upsert_buyer(merged)

    # ------------------------------------------------------------------ #
    # Campaigns
    # ------------------------------------------------------------------ #

    @staticmethod
    def _campaign_from_row(row: sqlite3.Row) -> Campaign:
        data = dict(row)
        data["recipients"] = _load_list(data["recipients"])
        data["thread_ids"] = _load_list(data["thread_ids"])
        return Campaign.from_dict(data)

    @staticmethod
    def _campaign_params(campaign: Campaign) -> tuple[Any, ...]:
        data = campaign.to_dict()
        return (
            data["offering_id"],
            data["kind"],
            _dump_list(data["recipients"]),
            data["subject"],
            data["personal_note"],
            data["status"],
            _to_iso(campaign.created_at),
            _to_iso(campaign.sent_at),
            _dump_list(data["thread_ids"]),
            data["error"],
        )

    def add_campaign(self, campaign: Campaign) -> None:
        """Insert a new campaign.

        Raises:
            ValueError: If a campaign with the same id already exists.
        """
        try:
            self._write(
                """
                INSERT INTO campaigns (
                    id, offering_id, kind, recipients, subject, personal_note,
                    status, created_at, sent_at, thread_ids, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (campaign.id, *self._campaign_params(campaign)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Campaign already exists: {campaign.id!r}") from exc

    def update_campaign(self, campaign: Campaign) -> None:
        """Replace every field of an existing campaign.

        Raises:
            KeyError: If no campaign has ``campaign.id``.
        """
        cursor = self._write(
            """
            UPDATE campaigns SET
                offering_id = ?, kind = ?, recipients = ?, subject = ?, personal_note = ?,
                status = ?, created_at = ?, sent_at = ?, thread_ids = ?, error = ?
            WHERE id = ?
            """,
            (*self._campaign_params(campaign), campaign.id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown campaign: {campaign.id!r}")

    def get_campaign(self, campaign_id: str) -> Campaign | None:
        """Return the campaign with ``campaign_id`` or ``None``."""
        row = self._execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return self._campaign_from_row(row) if row else None

    def list_campaigns(
        self,
        offering_id: str | None = None,
        status: CampaignStatus | None = None,
    ) -> list[Campaign]:
        """List campaigns, oldest first, optionally filtered by offering and/or status."""
        clauses: list[str] = []
        params: list[Any] = []
        if offering_id is not None:
            clauses.append("offering_id = ?")
            params.append(offering_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(_enum_value(status))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._execute(
            f"SELECT * FROM campaigns{where} ORDER BY created_at, id", tuple(params)
        ).fetchall()
        return [self._campaign_from_row(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Thread map
    # ------------------------------------------------------------------ #

    def map_thread(self, thread_id: str, offering_id: str, buyer_email: str = "") -> None:
        """Associate a mail thread with an offering (and optionally one buyer).

        Mapping the same thread again updates the offering. A blank
        ``buyer_email`` keeps a buyer recorded by an earlier mapping, so a
        blast chunk mapped after a personal send does not erase the buyer.
        """
        self._write(
            """
            INSERT INTO thread_map (thread_id, offering_id, buyer_email) VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                offering_id = excluded.offering_id,
                buyer_email = CASE
                    WHEN excluded.buyer_email = '' THEN thread_map.buyer_email
                    ELSE excluded.buyer_email
                END
            """,
            (thread_id, offering_id, _norm_email(buyer_email)),
        )

    def offering_for_thread(self, thread_id: str) -> str | None:
        """Return the offering id mapped to ``thread_id`` or ``None``."""
        row = self._execute(
            "SELECT offering_id FROM thread_map WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row["offering_id"] if row else None

    def buyer_for_thread(self, thread_id: str) -> str | None:
        """Return the buyer email mapped to ``thread_id`` or ``None`` if unknown/blank."""
        row = self._execute(
            "SELECT buyer_email FROM thread_map WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row["buyer_email"] or None if row else None

    # ------------------------------------------------------------------ #
    # Contacts / replies
    # ------------------------------------------------------------------ #

    def record_contact(self, contact: Contact) -> None:
        """Append a contact (touch) between the system and a buyer."""
        self._write(
            """
            INSERT INTO contacts (offering_id, buyer_email, kind, at, thread_id, message_id, campaign_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contact.offering_id,
                _norm_email(contact.buyer_email),
                _enum_value(contact.kind),
                _to_iso(contact.at),
                contact.thread_id,
                contact.message_id,
                contact.campaign_id,
            ),
        )

    def contacts(
        self,
        offering_id: str,
        buyer_email: str | None = None,
        kind: ContactKind | None = None,
    ) -> list[Contact]:
        """List contacts for an offering, oldest first, optionally per buyer and/or kind."""
        clauses = ["offering_id = ?"]
        params: list[Any] = [offering_id]
        if buyer_email is not None:
            clauses.append("buyer_email = ?")
            params.append(_norm_email(buyer_email))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(_enum_value(kind))
        rows = self._execute(
            f"SELECT * FROM contacts WHERE {' AND '.join(clauses)} ORDER BY at, id",
            tuple(params),
        ).fetchall()
        return [
            Contact(
                offering_id=r["offering_id"],
                buyer_email=r["buyer_email"],
                kind=ContactKind(r["kind"]),
                at=_from_iso(r["at"]),  # type: ignore[arg-type]
                thread_id=r["thread_id"],
                message_id=r["message_id"],
                campaign_id=r["campaign_id"],
            )
            for r in rows
        ]

    def record_reply(
        self,
        offering_id: str,
        buyer_email: str,
        message_id: str,
        intent: ReplyIntent,
        at: datetime,
    ) -> None:
        """Record that a buyer replied about an offering (with the classified intent)."""
        self._write(
            """
            INSERT INTO replies (offering_id, buyer_email, message_id, intent, at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (offering_id, _norm_email(buyer_email), message_id, _enum_value(intent), _to_iso(at)),
        )

    def has_replied(self, offering_id: str, buyer_email: str) -> bool:
        """Whether any reply from ``buyer_email`` about ``offering_id`` was recorded."""
        row = self._execute(
            "SELECT 1 FROM replies WHERE offering_id = ? AND buyer_email = ? LIMIT 1",
            (offering_id, _norm_email(buyer_email)),
        ).fetchone()
        return row is not None

    def buyers_due_follow_up(
        self,
        offering_id: str,
        before: datetime,
        max_follow_ups: int,
    ) -> list[str]:
        """Emails that should receive a follow-up about ``offering_id``.

        A buyer is due when their ``INITIAL`` contact for the offering happened
        before ``before``, no reply from them about the offering is recorded,
        fewer than ``max_follow_ups`` ``FOLLOW_UP`` contacts exist, and the
        buyer record exists with status ``ACTIVE``.

        Returns:
            Distinct emails, sorted.
        """
        rows = self._execute(
            """
            SELECT DISTINCT c.buyer_email AS email
            FROM contacts AS c
            JOIN buyers AS b ON b.email = c.buyer_email
            WHERE c.offering_id = ?
              AND c.kind = ?
              AND c.at < ?
              AND b.status = ?
              AND NOT EXISTS (
                    SELECT 1 FROM replies AS r
                    WHERE r.offering_id = c.offering_id AND r.buyer_email = c.buyer_email
              )
              AND (
                    SELECT COUNT(*) FROM contacts AS f
                    WHERE f.offering_id = c.offering_id
                      AND f.buyer_email = c.buyer_email
                      AND f.kind = ?
              ) < ?
            ORDER BY c.buyer_email
            """,
            (
                offering_id,
                ContactKind.INITIAL.value,
                _to_iso(before),
                BuyerStatus.ACTIVE.value,
                ContactKind.FOLLOW_UP.value,
                int(max_follow_ups),
            ),
        ).fetchall()
        return [r["email"] for r in rows]

    # ------------------------------------------------------------------ #
    # Inbox idempotency
    # ------------------------------------------------------------------ #

    def is_processed(self, message_id: str) -> bool:
        """Whether ``message_id`` has already been handled."""
        row = self._execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def mark_processed(self, message_id: str, at: datetime) -> None:
        """Remember that ``message_id`` was handled at ``at`` (first mark wins)."""
        self._write(
            "INSERT OR IGNORE INTO processed_messages (message_id, at) VALUES (?, ?)",
            (message_id, _to_iso(at)),
        )

    # ------------------------------------------------------------------ #
    # Audit log
    # ------------------------------------------------------------------ #

    def add_action(self, action: ActionRecord) -> ActionRecord:
        """Append an audit log entry and return it with ``id`` populated."""
        cursor = self._write(
            """
            INSERT INTO actions (kind, at, offering_id, buyer_email, thread_id, message_id, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _enum_value(action.kind),
                _to_iso(action.at),
                action.offering_id,
                _norm_email(action.buyer_email),
                action.thread_id,
                action.message_id,
                action.details,
            ),
        )
        action.id = cursor.lastrowid
        return action

    def list_actions(
        self,
        since: datetime | None = None,
        kind: ActionKind | None = None,
        limit: int | None = None,
    ) -> list[ActionRecord]:
        """List audit entries in chronological order.

        Args:
            since: Only actions at or after this time.
            kind: Only actions of this kind.
            limit: Return only the most recent ``limit`` matching actions
                (still ordered oldest first).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("at >= ?")
            params.append(_to_iso(since))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(_enum_value(kind))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        if limit is None:
            sql = f"SELECT * FROM actions{where} ORDER BY at, id"
        else:
            sql = (
                f"SELECT * FROM (SELECT * FROM actions{where} ORDER BY at DESC, id DESC LIMIT ?) "
                "ORDER BY at, id"
            )
            params.append(int(limit))
        rows = self._execute(sql, tuple(params)).fetchall()
        return [
            ActionRecord(
                kind=ActionKind(r["kind"]),
                at=_from_iso(r["at"]),  # type: ignore[arg-type]
                offering_id=r["offering_id"],
                buyer_email=r["buyer_email"],
                thread_id=r["thread_id"],
                message_id=r["message_id"],
                details=r["details"],
                id=r["id"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Key/value state
    # ------------------------------------------------------------------ #

    def get_state(self, key: str, default: str | None = None) -> str | None:
        """Return the stored value for ``key`` or ``default``."""
        row = self._execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, replacing any previous value."""
        self._write("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, str(value)))

    # ------------------------------------------------------------------ #
    # Daily send counters
    # ------------------------------------------------------------------ #

    def sends_on(self, day: date) -> int:
        """Number of messages recorded as sent on ``day`` (0 if none)."""
        row = self._execute("SELECT count FROM daily_sends WHERE day = ?", (_day_key(day),)).fetchone()
        return int(row["count"]) if row else 0

    def add_sends(self, day: date, count: int) -> None:
        """Add ``count`` to the send counter for ``day``."""
        key = _day_key(day)
        conn = self.connection
        conn.execute("INSERT OR IGNORE INTO daily_sends (day, count) VALUES (?, 0)", (key,))
        conn.execute("UPDATE daily_sends SET count = count + ? WHERE day = ?", (int(count), key))
        conn.commit()


__all__ = ["CSV_COLUMNS", "MEMORY_PATH", "Store", "TAG_SEPARATOR"]
