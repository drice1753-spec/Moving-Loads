"""Command-line interface: the ``offerings`` command.

Every operation of the email offerings system is reachable from here::

    offerings [--config PATH] [--db PATH] [--live] [--json] <command>
      init                                     create the database
      offering add (--file x.json | --sheet sheet.txt [--subject S]) [--activate]
      offering list [--status S]
      offering set-status ID STATUS
      offering preview ID [--internal] [--buyer EMAIL] [--note TEXT]
      buyers import FILE.csv
      buyers add EMAIL [--name N] [--company C] [--zip Z]
      buyers list [--status S]
      buyers unsubscribe EMAIL
      blast ID [--to EMAIL ...] [--note TEXT] [--kind blast|personal|update] [--now]
      internal ID                              schedule the cost sheet to the sales team
      run [--loop] [--interval SECONDS]        run_once (loop sleeps between passes)
      quote ID (--zip Z | --dest "City, ST") [--truckloads N]
      digest [--since-hours H] [--send]
      status                                   counts of offerings/buyers/campaigns/recent actions

:func:`main` builds ``Settings.from_env(path=--config)``, applies ``--live``
to ``policy.live`` (refusing to continue without Gmail credentials) and
``--db`` to ``db_path``, then lazily opens the :class:`Store`, the mailer
(``build_mailer``: a dry-run outbox unless live), the classifier and the
:class:`Engine` as the chosen command needs them.

Output goes to stdout; with ``--json`` every command prints one
machine-readable JSON document instead of text. Expected failures
(``ValueError``, ``KeyError``, file errors, ``sqlite3.Error``,
``MailerError``, ``EngineError``) are reported as a one-line ``error: ...``
message on stderr with exit code 1, never as a traceback. Argument errors
exit with code 2 the way ``argparse`` always does.

Safety: nothing here can bypass the engine. Dry run is the default, ``--live``
is the only way to reach Gmail from the command line, ``status --json``
redacts secrets, and ``run --loop`` stops cleanly on ``Ctrl-C``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from email_offerings.classify import build_classifier
from email_offerings.config import Settings
from email_offerings.engine import Engine, EngineError
from email_offerings.mailer import Mailer, MailerError, build_mailer
from email_offerings.models import (
    ActionRecord,
    Buyer,
    BuyerStatus,
    Campaign,
    CampaignKind,
    CampaignStatus,
    Offering,
    OfferingStatus,
    RunReport,
    Unit,
    utcnow,
)
from email_offerings.parsers import load_offering_file, parse_internal_sheet
from email_offerings.store import Store
from email_offerings.templates import (
    RenderedEmail,
    format_price,
    render_internal_sheet,
    render_offering_email,
)

__all__ = [
    "DEFAULT_DIGEST_HOURS",
    "DEFAULT_LOOP_INTERVAL",
    "HANDLED_ERRORS",
    "PROG",
    "STATUS_ACTION_COUNT",
    "build_parser",
    "load_settings",
    "main",
    "read_sheet_file",
]

#: Program name used in help and error output.
PROG = "offerings"
#: Seconds between passes of ``run --loop`` when ``--interval`` is omitted.
DEFAULT_LOOP_INTERVAL = 900.0
#: Window of ``digest`` when ``--since-hours`` is omitted.
DEFAULT_DIGEST_HOURS = 24.0
#: Number of recent audit-log entries ``status`` shows.
STATUS_ACTION_COUNT = 10
#: Exceptions :func:`main` turns into a one-line stderr message and exit code 1.
HANDLED_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,
    KeyError,
    OSError,
    sqlite3.Error,
    MailerError,
    EngineError,
)

_SUBJECT_LINE_RE = re.compile(r"^\s*subject\s*:\s*(?P<subject>.*?)\s*$", re.IGNORECASE)
_BLAST_KINDS = (CampaignKind.BLAST.value, CampaignKind.PERSONAL.value, CampaignKind.UPDATE.value)


# --------------------------------------------------------------------------- #
# Settings and runtime objects
# --------------------------------------------------------------------------- #


def load_settings(args: argparse.Namespace) -> Settings:
    """Build :class:`Settings` from the global options.

    ``Settings.from_env(path=args.config)`` reads the optional JSON file and
    overlays ``OFFERINGS_*`` environment variables. ``--live`` sets
    ``policy.live`` and immediately calls ``validate_for_live`` so a missing
    Gmail credential fails before anything is opened; ``--db`` overrides
    ``db_path``.

    Raises:
        FileNotFoundError: If ``--config`` names a file that does not exist.
        ValueError: If no sender is configured, a setting is invalid, or
            ``--live`` is requested without Gmail credentials.
    """
    config = getattr(args, "config", None)
    if config and not Path(config).is_file():
        raise FileNotFoundError(f"Config file not found: {config}")
    settings = Settings.from_env(path=config)
    if getattr(args, "live", False):
        settings.policy.live = True
        settings.validate_for_live()
    db = getattr(args, "db", None)
    if db:
        settings.db_path = str(db)
    return settings


class _App:
    """Runtime objects shared by the command handlers, built lazily.

    Only what a command touches is created: ``init`` opens the store but
    never builds a mailer; ``status`` in dry run never builds the classifier.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.settings = load_settings(args)
        self.json_output = bool(getattr(args, "json", False))
        self._store: Store | None = None
        self._mailer: Mailer | None = None
        self._engine: Engine | None = None

    @property
    def store(self) -> Store:
        """The SQLite store at ``settings.db_path`` (opened on first use)."""
        if self._store is None:
            self._store = Store(self.settings.db_path)
        return self._store

    @property
    def mailer(self) -> Mailer:
        """``build_mailer(settings)``: a dry-run outbox unless ``--live``."""
        if self._mailer is None:
            self._mailer = build_mailer(self.settings)
        return self._mailer

    @property
    def engine(self) -> Engine:
        """The engine over the store, the mailer and ``build_classifier(settings)``."""
        if self._engine is None:
            self._engine = Engine(self.settings, self.store, self.mailer, build_classifier(self.settings))
        return self._engine

    def close(self) -> None:
        """Close the store if it was opened."""
        if self._store is not None:
            self._store.close()
            self._store = None

    def emit(self, lines: Sequence[str], data: Any) -> None:
        """Print ``lines`` as text, or ``data`` as JSON when ``--json`` is set."""
        if self.json_output:
            print(json.dumps(_jsonable(data), indent=2))
        else:
            print("\n".join(lines))


Handler = Callable[[_App, argparse.Namespace], int]


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def _jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, enums, datetimes and paths for ``json.dumps``."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    """Left-aligned text table; an empty ``rows`` gives an empty list."""
    if not rows:
        return []
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [max(len(header), *(len(row[i]) for row in cells)) for i, header in enumerate(headers)]
    template = "  ".join(f"{{:<{width}}}" for width in widths)
    lines = [template.format(*headers).rstrip()]
    lines.extend(template.format(*row).rstrip() for row in cells)
    return lines


def _counts(values: Iterable[Enum], members: type[Enum]) -> dict[str, int]:
    """``{status: count}`` in enum order, omitting zero counts."""
    counter = Counter(values)
    return {member.value: counter[member] for member in members if counter[member]}


def _counts_phrase(counts: dict[str, int]) -> str:
    """``"active=2, draft=1"`` or ``"none"``."""
    return ", ".join(f"{key}={value}" for key, value in counts.items()) or "none"


def _number(value: float) -> str:
    """``2.0`` → ``"2"``, ``40000.0`` → ``"40,000"``, ``1.5`` → ``"1.5"``."""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _local_today(settings: Settings) -> date:
    """Today's date in ``settings.timezone`` (UTC when the zone is unknown), as the engine counts it."""
    try:
        zone: Any = ZoneInfo(settings.timezone)
    except Exception:  # ZoneInfoNotFoundError, missing tzdata, bad name
        zone = timezone.utc
    return utcnow().astimezone(zone).date()


def _action_line(action: ActionRecord) -> str:
    """One audit-log entry for ``status``."""
    when = action.at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    parts = [when, f"{action.kind.value:<18}"]
    if action.offering_id:
        parts.append(action.offering_id)
    if action.buyer_email:
        parts.append(action.buyer_email)
    line = " ".join(parts)
    if action.details.strip():
        line += f": {action.details.strip()}"
    return line


def _rendered_lines(rendered: RenderedEmail) -> list[str]:
    return [f"Subject: {rendered.subject}", "", rendered.text]


def _rendered_data(rendered: RenderedEmail) -> dict[str, Any]:
    return {"subject": rendered.subject, "text": rendered.text, "html": rendered.html}


def _campaign_lines(campaign: Campaign) -> list[str]:
    lines = [
        f"Scheduled campaign {campaign.id} ({campaign.kind.value}): {len(campaign.recipients)} recipient(s)",
        f"  Subject: {campaign.subject}",
    ]
    if campaign.kind is CampaignKind.PERSONAL or campaign.kind is CampaignKind.INTERNAL:
        lines.append(f"  To: {', '.join(campaign.recipients)}")
    return lines


def _require_offering(store: Store, offering_id: str) -> Offering:
    """Fetch an offering or raise ``ValueError`` with a user-facing message."""
    offering = store.get_offering(offering_id)
    if offering is None:
        raise ValueError(f"Unknown offering: {offering_id!r} (see 'offerings offering list').")
    return offering


def read_sheet_file(path: str, subject: str = "") -> tuple[str, str]:
    """Read an internal cost-sheet text file into ``(subject, body)``.

    When the first non-blank line is ``Subject: ...`` it supplies the subject
    (unless an explicit ``subject`` is given, which wins) and is removed from
    the body.

    Args:
        path: Path to the text file.
        subject: Explicit subject from ``--subject`` (may be blank).

    Returns:
        ``(subject, body)`` with the body stripped of surrounding blank lines.

    Raises:
        ValueError: If no subject is available from either source.
        OSError: If the file cannot be read.
    """
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    subject = subject.strip()
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is not None:
        match = _SUBJECT_LINE_RE.match(lines[first])
        if match:
            if not subject:
                subject = match.group("subject").strip()
            lines = lines[first + 1 :]
    if not subject:
        raise ValueError(
            f"{path} has no 'Subject: ...' first line; pass --subject '$0.99/sf Title (AWR 10/1)'."
        )
    return subject, "\n".join(lines).strip("\n")


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


def cmd_init(app: _App, args: argparse.Namespace) -> int:
    """``init``: create the database (idempotent)."""
    path = app.settings.db_path
    existed = path != ":memory:" and Path(path).expanduser().is_file()
    store = app.store
    verb = "Database already initialised" if existed else "Database created"
    app.emit([f"{verb}: {store.path}"], {"db_path": store.path, "created": not existed})
    return 0


def cmd_offering_add(app: _App, args: argparse.Namespace) -> int:
    """``offering add``: load a JSON offering file or parse an internal cost sheet."""
    now = utcnow()
    if args.file:
        offering = load_offering_file(args.file)
        source = args.file
    else:
        subject, body = read_sheet_file(args.sheet, args.subject or "")
        offering = parse_internal_sheet(subject, body, now=now)
        source = args.sheet
    if args.activate:
        offering.status = OfferingStatus.ACTIVE
    existed = app.store.get_offering(offering.id) is not None
    app.store.upsert_offering(offering)
    verb = "Updated" if existed else "Added"
    lines = [
        f"{verb} offering {offering.id} [{offering.status.value}] from {source}",
        f"  Title: {offering.title}",
        f"  Price: {format_price(offering.sell_price, offering.unit)} FOB {offering.fob_location}",
    ]
    if offering.quantity_available:
        lines.append(f"  Quantity: {offering.quantity_available}")
    if offering.make_offers:
        lines.append("  Make offers: yes")
    if offering.status is not OfferingStatus.ACTIVE:
        lines.append("  Draft: 'blast' activates it, or use 'offering set-status ID active'.")
    app.emit(lines, {"created": not existed, "offering": offering.to_dict()})
    return 0


def cmd_offering_list(app: _App, args: argparse.Namespace) -> int:
    """``offering list [--status S]``."""
    status = OfferingStatus(args.status) if args.status else None
    offerings = app.store.list_offerings(status)
    rows = [
        (o.id, o.status.value, format_price(o.sell_price, o.unit), o.fob_location, o.title)
        for o in offerings
    ]
    lines = _table(("ID", "STATUS", "PRICE", "FOB", "TITLE"), rows) or ["(no offerings)"]
    app.emit(lines, [o.to_dict() for o in offerings])
    return 0


def cmd_offering_set_status(app: _App, args: argparse.Namespace) -> int:
    """``offering set-status ID STATUS``."""
    status = OfferingStatus(args.status)
    app.store.set_offering_status(args.id, status)
    app.emit([f"Offering {args.id} is now {status.value}"], {"id": args.id, "status": status.value})
    return 0


def cmd_offering_preview(app: _App, args: argparse.Namespace) -> int:
    """``offering preview ID [--internal] [--buyer EMAIL] [--note TEXT]``."""
    offering = _require_offering(app.store, args.id)
    now = utcnow()
    if args.internal:
        rendered = render_internal_sheet(offering, app.settings, when=now)
        to = app.settings.sales_team_email or "(sales_team_email not set)"
    else:
        buyer: Buyer | None = None
        if args.buyer:
            buyer = app.store.get_buyer(args.buyer) or Buyer(email=args.buyer)
        rendered = render_offering_email(
            offering, app.settings, when=now, buyer=buyer, personal_note=args.note or ""
        )
        to = buyer.email if buyer is not None else f"{app.settings.sender_email} (Bcc: buyer list)"
    app.emit([f"To: {to}", *_rendered_lines(rendered)], {"to": to, **_rendered_data(rendered)})
    return 0


def cmd_buyers_import(app: _App, args: argparse.Namespace) -> int:
    """``buyers import FILE.csv``."""
    imported, rejected = app.store.import_buyers_csv(args.file)
    lines = [f"Imported {imported} buyer(s) from {args.file}"]
    if rejected:
        lines.append(f"Rejected {len(rejected)} line(s) without a valid email:")
        lines.extend(f"  {line}" for line in rejected)
    app.emit(lines, {"imported": imported, "rejected": rejected})
    return 0


def cmd_buyers_add(app: _App, args: argparse.Namespace) -> int:
    """``buyers add EMAIL [--name N] [--company C] [--zip Z]`` (merges into an existing buyer)."""
    incoming = Buyer(
        email=args.email,
        name=args.name or "",
        company=args.company or "",
        postal_code=args.zip or "",
    )
    existing = app.store.get_buyer(incoming.email)
    if existing is None:
        buyer = incoming
    else:
        buyer = Buyer(
            email=existing.email,
            name=incoming.name or existing.name,
            company=incoming.company or existing.company,
            postal_code=incoming.postal_code or existing.postal_code,
            city_state=existing.city_state,
            status=existing.status,
            tags=list(existing.tags),
            notes=existing.notes,
            created_at=existing.created_at,
        )
    app.store.upsert_buyer(buyer)
    verb = "Added" if existing is None else "Updated"
    details = ", ".join(part for part in (buyer.name, buyer.company, buyer.postal_code) if part)
    line = f"{verb} buyer {buyer.email} [{buyer.status.value}]" + (f": {details}" if details else "")
    app.emit([line], {"created": existing is None, "buyer": buyer.to_dict()})
    return 0


def cmd_buyers_list(app: _App, args: argparse.Namespace) -> int:
    """``buyers list [--status S]`` (every status unless filtered)."""
    status = BuyerStatus(args.status) if args.status else None
    buyers = app.store.list_buyers(status)
    rows = [
        (b.email, b.status.value, b.name, b.company, b.postal_code, b.city_state, "|".join(b.tags))
        for b in buyers
    ]
    headers = ("EMAIL", "STATUS", "NAME", "COMPANY", "ZIP", "CITY", "TAGS")
    lines = _table(headers, rows) or ["(no buyers)"]
    lines.append(f"{len(buyers)} buyer(s)")
    app.emit(lines, [b.to_dict() for b in buyers])
    return 0


def cmd_buyers_unsubscribe(app: _App, args: argparse.Namespace) -> int:
    """``buyers unsubscribe EMAIL``: permanent suppression, same as a REMOVE reply."""
    app.store.set_buyer_status(args.email, BuyerStatus.UNSUBSCRIBED)
    email = args.email.strip().lower()
    app.emit(
        [f"Unsubscribed {email}; they will receive nothing further."],
        {"email": email, "status": BuyerStatus.UNSUBSCRIBED.value},
    )
    return 0


def cmd_blast(app: _App, args: argparse.Namespace) -> int:
    """``blast ID [--to EMAIL ...] [--note TEXT] [--kind K] [--now]``."""
    kind = CampaignKind(args.kind)
    campaign = app.engine.schedule_blast(
        args.id, kind=kind, recipients=args.to or None, personal_note=args.note or ""
    )
    lines = _campaign_lines(campaign)
    data: dict[str, Any] = {"campaign": campaign.to_dict(), "recipients": len(campaign.recipients)}
    if args.now:
        report = app.engine.run_once()
        campaign = app.store.get_campaign(campaign.id) or campaign
        lines.append(report.summary())
        state = f"Campaign {campaign.id} is now {campaign.status.value}"
        if campaign.error:
            state += f" ({campaign.error})"
        lines.append(state)
        data["campaign"] = campaign.to_dict()
        data["report"] = report
    else:
        lines.append(f"Run '{PROG} run' to send it.")
    app.emit(lines, data)
    return 0


def cmd_internal(app: _App, args: argparse.Namespace) -> int:
    """``internal ID``: schedule the DELETE RED cost sheet to ``sales_team_email``."""
    campaign = app.engine.schedule_internal_sheet(args.id)
    lines = [*_campaign_lines(campaign), f"Run '{PROG} run' to send it."]
    app.emit(lines, {"campaign": campaign.to_dict(), "recipients": len(campaign.recipients)})
    return 0


def _emit_report(app: _App, report: RunReport) -> None:
    if app.json_output:
        print(json.dumps(_jsonable(report), indent=2))
    else:
        print(report.summary())
        for error in report.errors:
            print(f"  error: {error}")
    sys.stdout.flush()


def cmd_run(app: _App, args: argparse.Namespace) -> int:
    """``run [--loop] [--interval SECONDS]``: one pass, or passes until Ctrl-C."""
    try:
        while True:
            report = app.engine.run_once()
            _emit_report(app, report)
            if not args.loop:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Interrupted; stopping the loop.", file=sys.stderr)
    return 0


def cmd_quote(app: _App, args: argparse.Namespace) -> int:
    """``quote ID (--zip Z | --dest "City, ST") [--truckloads N]``."""
    offering = _require_offering(app.store, args.id)
    destination = args.zip or args.dest
    quote = app.engine.quote(offering, destination, args.truckloads)
    loads = _number(quote.truckloads)
    lines = [
        f"Offering: {offering.id} - {offering.title}",
        f"Route: {quote.origin} -> {quote.destination}: {quote.distance_miles:,.1f} mi",
        (
            f"Freight: ${quote.freight_per_truckload:,.0f} per truckload "
            f"(${quote.rate_per_mile:,.2f}/mi) x {loads} = ${quote.freight_total:,.0f}"
        ),
        f"FOB price: {format_price(quote.fob_price_per_unit, quote.unit)}",
        f"Delivered price: {format_price(quote.delivered_price_per_unit, quote.unit)}",
    ]
    if quote.delivered_total is not None:
        if quote.unit is Unit.TRUCKLOAD or quote.units_total is None:
            lines.append(f"Total: ${quote.delivered_total:,.2f} delivered for {loads} truckload(s)")
        else:
            lines.append(
                f"Total: {_number(quote.units_total)} {quote.unit.value} "
                f"for ${quote.delivered_total:,.2f} delivered"
            )
    app.emit(lines, quote)
    return 0


def cmd_digest(app: _App, args: argparse.Namespace) -> int:
    """``digest [--since-hours H] [--send]``."""
    hours = float(args.since_hours)
    if hours <= 0:
        raise ValueError("--since-hours must be positive.")
    since = utcnow() - timedelta(hours=hours)
    rendered = app.engine.digest(since=since)
    lines = _rendered_lines(rendered)
    data: dict[str, Any] = {"since": since, **_rendered_data(rendered), "sent": False}
    if args.send:
        result = app.engine.send_digest(since=since)
        mode = "dry run, see the outbox" if result.dry_run else f"message {result.message_id}"
        lines.extend(["", f"Digest sent to {app.settings.escalation_email} ({mode})."])
        data.update(sent=True, to=app.settings.escalation_email, result=result)
    app.emit(lines, data)
    return 0


def _check_gmail(mailer: Mailer) -> str:
    """In live mode, obtain an access token so credential problems surface in ``status``."""
    token_check = getattr(mailer, "access_token", None)
    if not callable(token_check):
        return "Gmail: transport does not expose a credential check"
    token_check()
    return "Gmail credentials: OK (access token obtained)"


def cmd_status(app: _App, args: argparse.Namespace) -> int:
    """``status``: counts of offerings/buyers/campaigns, today's sends and the last actions."""
    settings = app.settings
    store = app.store
    live = settings.policy.live
    gmail_line = _check_gmail(app.mailer) if live else None

    offerings = store.list_offerings()
    buyers = store.list_buyers(None)
    campaigns = store.list_campaigns()
    offering_counts = _counts((o.status for o in offerings), OfferingStatus)
    buyer_counts = _counts((b.status for b in buyers), BuyerStatus)
    campaign_counts = _counts((c.status for c in campaigns), CampaignStatus)
    today = _local_today(settings)
    sends_today = store.sends_on(today)
    actions = store.list_actions(limit=STATUS_ACTION_COUNT)

    mode = f"LIVE (Gmail as {settings.sender_email})" if live else f"DRY RUN (outbox: {settings.outbox_dir})"
    lines = [f"Mode: {mode}"]
    if gmail_line:
        lines.append(gmail_line)
    lines.extend(
        [
            f"Database: {store.path}",
            f"Classifier: {settings.classifier}; auto-reply mode: {settings.policy.auto_reply_mode}",
            f"Offerings: {len(offerings)} ({_counts_phrase(offering_counts)})",
            f"Buyers: {len(buyers)} ({_counts_phrase(buyer_counts)})",
            f"Campaigns: {len(campaigns)} ({_counts_phrase(campaign_counts)})",
            f"Sends today ({today.isoformat()}): {sends_today} of max {settings.policy.max_sends_per_day}",
            f"Recent actions (last {len(actions)}):",
        ]
    )
    lines.extend(f"  {_action_line(action)}" for action in actions)
    if not actions:
        lines.append("  (none)")

    data = {
        "mode": "live" if live else "dry_run",
        "live": live,
        "gmail": gmail_line,
        "db_path": store.path,
        "settings": settings.to_dict(redact=True),
        "offerings": {"total": len(offerings), "by_status": offering_counts},
        "buyers": {"total": len(buyers), "by_status": buyer_counts},
        "campaigns": {"total": len(campaigns), "by_status": campaign_counts},
        "sends_today": {"date": today, "count": sends_today, "max_sends_per_day": settings.policy.max_sends_per_day},
        "actions": actions,
    }
    app.emit(lines, data)
    return 0


_HANDLERS: dict[tuple[str, str | None], Handler] = {
    ("init", None): cmd_init,
    ("offering", "add"): cmd_offering_add,
    ("offering", "list"): cmd_offering_list,
    ("offering", "set-status"): cmd_offering_set_status,
    ("offering", "preview"): cmd_offering_preview,
    ("buyers", "import"): cmd_buyers_import,
    ("buyers", "add"): cmd_buyers_add,
    ("buyers", "list"): cmd_buyers_list,
    ("buyers", "unsubscribe"): cmd_buyers_unsubscribe,
    ("blast", None): cmd_blast,
    ("internal", None): cmd_internal,
    ("run", None): cmd_run,
    ("quote", None): cmd_quote,
    ("digest", None): cmd_digest,
    ("status", None): cmd_status,
}


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #


def _non_negative(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a number, got {text!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the ``offerings`` argument parser (see the module docstring for the grammar)."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Autonomous email offerings: blast deals, answer replies, quote freight, escalate offers.",
        epilog="Dry run is the default: nothing reaches Gmail without --live (or policy.live).",
    )
    parser.add_argument("--config", metavar="PATH", help="JSON settings file (default: $OFFERINGS_CONFIG)")
    parser.add_argument("--db", metavar="PATH", help="SQLite database (overrides db_path)")
    parser.add_argument("--live", action="store_true", help="send through Gmail instead of the dry-run outbox")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    commands.add_parser("init", help="create the database")

    offering = commands.add_parser("offering", help="manage offerings")
    offering_commands = offering.add_subparsers(dest="offering_command", metavar="<subcommand>", required=True)
    add = offering_commands.add_parser("add", help="add an offering from a JSON file or an internal cost sheet")
    source = add.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", metavar="X.JSON", help="offering JSON file (Offering.to_dict shape)")
    source.add_argument("--sheet", metavar="SHEET.TXT", help="internal cost-sheet email saved as text")
    add.add_argument("--subject", metavar="S", help="subject of the sheet (when the file has no 'Subject:' line)")
    add.add_argument("--activate", action="store_true", help="store it as active instead of draft")
    listing = offering_commands.add_parser("list", help="list offerings")
    listing.add_argument("--status", choices=[s.value for s in OfferingStatus], help="only this status")
    set_status = offering_commands.add_parser("set-status", help="change an offering's status")
    set_status.add_argument("id", metavar="ID")
    set_status.add_argument("status", metavar="STATUS", choices=[s.value for s in OfferingStatus])
    preview = offering_commands.add_parser("preview", help="render the email without scheduling anything")
    preview.add_argument("id", metavar="ID")
    target = preview.add_mutually_exclusive_group()
    target.add_argument("--internal", action="store_true", help="the DELETE RED cost sheet for the sales team")
    target.add_argument("--buyer", metavar="EMAIL", help="personal forward to this buyer")
    preview.add_argument("--note", metavar="TEXT", help="one-line note placed first in the body")

    buyers = commands.add_parser("buyers", help="manage the buyer list")
    buyers_commands = buyers.add_subparsers(dest="buyers_command", metavar="<subcommand>", required=True)
    import_ = buyers_commands.add_parser("import", help="import buyers from a CSV with an 'email' column")
    import_.add_argument("file", metavar="FILE.CSV")
    badd = buyers_commands.add_parser("add", help="add or update one buyer")
    badd.add_argument("email", metavar="EMAIL")
    badd.add_argument("--name", metavar="N")
    badd.add_argument("--company", metavar="C")
    badd.add_argument("--zip", metavar="Z", help="ZIP code used for delivered quotes")
    blist = buyers_commands.add_parser("list", help="list buyers")
    blist.add_argument("--status", choices=[s.value for s in BuyerStatus], help="only this status")
    unsub = buyers_commands.add_parser("unsubscribe", help="suppress a buyer permanently")
    unsub.add_argument("email", metavar="EMAIL")

    blast = commands.add_parser("blast", help="schedule an offering to buyers")
    blast.add_argument("id", metavar="ID")
    blast.add_argument(
        "--to", metavar="EMAIL", nargs="+", action="extend", help="explicit recipients (default: all active buyers)"
    )
    blast.add_argument("--note", metavar="TEXT", help="one-line personal note placed first")
    blast.add_argument("--kind", choices=_BLAST_KINDS, default=CampaignKind.BLAST.value, help="default: blast")
    blast.add_argument("--now", action="store_true", help="run a pass right away instead of waiting for 'run'")

    internal = commands.add_parser("internal", help="schedule the cost sheet to the sales team")
    internal.add_argument("id", metavar="ID")

    run = commands.add_parser("run", help="one pass: expire, dispatch campaigns, read replies, follow up")
    run.add_argument("--loop", action="store_true", help="keep running passes until Ctrl-C")
    run.add_argument(
        "--interval",
        metavar="SECONDS",
        type=_non_negative,
        default=DEFAULT_LOOP_INTERVAL,
        help=f"seconds between passes with --loop (default {DEFAULT_LOOP_INTERVAL:g})",
    )

    quote = commands.add_parser("quote", help="delivered price for an offering to a destination")
    quote.add_argument("id", metavar="ID")
    where = quote.add_mutually_exclusive_group(required=True)
    where.add_argument("--zip", metavar="Z", help="destination ZIP code")
    where.add_argument("--dest", metavar="CITY_ST", help='destination such as "Oklahoma City, OK"')
    quote.add_argument("--truckloads", metavar="N", type=float, default=1.0, help="default 1")

    digest = commands.add_parser("digest", help="summary of actions, offers and drafts awaiting review")
    digest.add_argument(
        "--since-hours", metavar="H", type=float, default=DEFAULT_DIGEST_HOURS, help=f"default {DEFAULT_DIGEST_HOURS:g}"
    )
    digest.add_argument("--send", action="store_true", help="also email it to escalation_email")

    commands.add_parser("status", help="counts of offerings, buyers, campaigns and recent actions")
    return parser


def _command_key(args: argparse.Namespace) -> tuple[str, str | None]:
    return args.command, getattr(args, f"{args.command}_command", None)


def _error_message(exc: BaseException) -> str:
    """One-line, quote-free message for a handled exception."""
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or type(exc).__name__


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    """Run the ``offerings`` command.

    Args:
        argv: Command-line arguments without the program name; ``None`` uses
            ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` after a handled error (message on stderr).
        Argument errors exit with code 2 via ``argparse``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS[_command_key(args)]
    app: _App | None = None
    try:
        app = _App(args)
        return handler(app, args)
    except HANDLED_ERRORS as exc:
        print(f"error: {_error_message(exc)}", file=sys.stderr)
        return 1
    finally:
        if app is not None:
            app.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
