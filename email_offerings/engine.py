"""The autonomous loop: send campaigns, read replies, quote freight, follow up.

:class:`Engine` ties the other modules together. One :meth:`Engine.run_once`
pass does, in order:

1. :meth:`Engine.expire_offerings` -- ``ACTIVE`` offerings past ``expires_at``
   (or ``created_at + Policy.offering_ttl_days``) become ``EXPIRED`` and their
   ``SCHEDULED`` campaigns are cancelled.
2. :meth:`Engine.dispatch_campaigns` -- ``SCHEDULED`` campaigns go out: blasts
   and updates as Bcc chunks of ``Policy.bcc_chunk_size`` addressed to the
   sender, personal forwards / follow-ups / internal cost sheets one message
   per recipient.
3. :meth:`Engine.process_inbox` -- new inbound mail is matched to an offering
   (thread map first, then the subject), classified and routed by
   :meth:`Engine.handle_inbound`.
4. :meth:`Engine.send_follow_ups` -- buyers who never answered get a nudge
   after ``Policy.follow_up_after_days`` (at most ``Policy.max_follow_ups``).

Safety model (docs/EMAIL_OFFERINGS_DESIGN.md, section 2):

* Every send passes :meth:`Engine._can_send` -- the per-run cap, the per-day
  cap and quiet hours evaluated in ``Settings.timezone`` -- and recipient
  filtering (buyer status and ``Policy.allowed_recipient_domains``).
  Owner-facing mail (escalations, digests) ignores quiet hours but still
  counts against the caps. A buyer-facing reply that is blocked by a cap is
  drafted instead of dropped.
* Bounces, auto-replies and unsubscribe requests are detected with the rule
  helpers of :mod:`email_offerings.classify` *before* the configured
  classifier runs, so they can never trigger a reply whatever the
  classifier says. Unsubscribe is checked before any other intent.
* Firm offers are escalated to ``Settings.escalation_email`` and the thread
  is labelled; the only buyer-facing reaction is an optional acknowledgement
  (``Policy.acknowledge_offers``). Offers are never accepted automatically.
* Buyer-facing automatic replies honour ``Policy.auto_reply_mode``
  (``"draft"`` by default). Questions, unknown replies and failed quotes
  become drafts with the original quoted, for a human to finish, even in
  ``"send"`` mode.
* Each inbound message is handled at most once (``Store.is_processed``) and
  mail from ``Settings.sender_email`` is never treated as a reply.
* Every per-item failure is recorded as ``ActionKind.ERROR`` and collected in
  ``RunReport.errors``; :meth:`Engine.run_once` never raises because one
  reply, one chunk or one follow-up failed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from email_offerings.classify import (
    Classifier,
    is_auto_reply,
    is_bounce,
    is_unsubscribe,
    strip_quoted_text,
)
from email_offerings.config import Settings
from email_offerings.mailer import Mailer
from email_offerings.models import (
    ActionKind,
    ActionRecord,
    Buyer,
    BuyerStatus,
    Campaign,
    CampaignKind,
    CampaignStatus,
    Classification,
    Contact,
    ContactKind,
    DeliveredQuote,
    InboundMessage,
    Offering,
    OfferingStatus,
    OutboundMessage,
    ReplyIntent,
    RunReport,
    SendResult,
    utcnow,
)
from email_offerings.pricing import (
    DistanceProvider,
    google_distance_provider,
    normalize_destination,
    quote_delivered,
)
from email_offerings.store import Store
from email_offerings.templates import (
    RenderedEmail,
    blast_subject,
    internal_subject,
    personal_subject,
    render_delivered_quote_reply,
    render_digest,
    render_follow_up,
    render_interested_reply,
    render_internal_sheet,
    render_offer_acknowledgement,
    render_offer_escalation,
    render_offering_email,
    render_zip_request_reply,
    signature_block,
    text_to_html,
)

__all__ = [
    "Engine",
    "EngineError",
    "LAST_INBOX_POLL_KEY",
    "NEEDS_REPLY_LABEL",
    "OFFER_LABEL",
]

#: ``Store`` state key holding the date of the newest inbound message seen.
LAST_INBOX_POLL_KEY = "last_inbox_poll"
#: Label (under ``Settings.gmail_label_prefix``) for threads with a firm offer.
OFFER_LABEL = "Offer"
#: Label (under ``Settings.gmail_label_prefix``) for threads a human must answer.
NEEDS_REPLY_LABEL = "Needs reply"

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|aw|wg)\s*:\s*)+", re.IGNORECASE)
_RE_PREFIX_RE = re.compile(r"^\s*re\s*:", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_CAMPAIGN_ACTION: dict[CampaignKind, ActionKind] = {
    CampaignKind.BLAST: ActionKind.BLAST_SENT,
    CampaignKind.UPDATE: ActionKind.BLAST_SENT,
    CampaignKind.PERSONAL: ActionKind.PERSONAL_SENT,
    CampaignKind.FOLLOW_UP: ActionKind.FOLLOW_UP_SENT,
    CampaignKind.INTERNAL: ActionKind.PERSONAL_SENT,
}


class EngineError(RuntimeError):
    """Raised when the engine refuses an operation (missing configuration, cap reached)."""


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def _ensure_aware(when: datetime) -> datetime:
    """Treat a naive datetime as UTC; return aware datetimes unchanged."""
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _normalise_subject(subject: str) -> str:
    """Strip ``Re:``/``Fwd:`` prefixes, collapse whitespace and lower-case."""
    return _WS_RE.sub(" ", _SUBJECT_PREFIX_RE.sub("", subject or "")).strip().lower()


def _reply_subject(inbound: InboundMessage, rendered: RenderedEmail) -> str:
    """``"Re: {inbound.subject}"`` unless it already starts with ``Re:``; template subject if blank."""
    subject = (inbound.subject or "").strip()
    if not subject:
        return rendered.subject
    if _RE_PREFIX_RE.match(subject):
        return subject
    return f"Re: {subject}"


def _greeting(buyer: Buyer | None) -> str:
    if buyer is not None and buyer.first_name:
        return f"Hi {buyer.first_name},"
    return "Hello,"


def _quoted_original(inbound: InboundMessage) -> str:
    """The inbound message quoted with ``> `` the way a mail client does."""
    name = inbound.from_name.strip()
    who = f"{name} <{inbound.from_email}>" if name else inbound.from_email
    lines = [f"> {line}" for line in inbound.body_text.strip().splitlines()] or ["> (empty message)"]
    return f"On {inbound.date.isoformat(timespec='minutes')}, {who} wrote:\n" + "\n".join(lines)


def _clean_recipients(recipients: list[str]) -> list[str]:
    """Strip, lower-case, de-duplicate and validate explicit recipient addresses.

    Raises:
        ValueError: If an address is not of the form ``local@domain.tld``.
    """
    cleaned: list[str] = []
    for raw in recipients:
        email = (raw or "").strip().lower()
        if not email:
            continue
        if not _EMAIL_RE.match(email):
            raise ValueError(f"Invalid recipient address: {raw!r}")
        if email not in cleaned:
            cleaned.append(email)
    return cleaned


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class Engine:
    """The autonomous loop. See the module docstring for the order of operations.

    Args:
        settings: Sender identity, policy and integration settings.
        store: Persistence (offerings, buyers, campaigns, audit log).
        mailer: Transport; :class:`~email_offerings.mailer.DryRunMailer`
            unless ``settings.policy.live`` is set (see ``build_mailer``).
        classifier: Reply classifier (rules or Claude).
        distance_provider: ``(origin, destination) -> miles``. ``None`` uses
            the Google Distance Matrix when ``settings.google_maps_api_key``
            is set; otherwise delivered quotes fail and are drafted for a human.
        clock: Returns the current time (aware UTC); injectable for tests.
    """

    def __init__(
        self,
        settings: Settings,
        store: Store,
        mailer: Mailer,
        classifier: Classifier,
        *,
        distance_provider: DistanceProvider | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self.store = store
        self.mailer = mailer
        self.classifier = classifier
        self.clock = clock
        if distance_provider is None and settings.freight_configured:
            distance_provider = google_distance_provider(settings.google_maps_api_key)
        self.distance_provider: DistanceProvider | None = distance_provider
        self._sent_this_run = 0

    # ------------------------------------------------------------------ #
    # Time
    # ------------------------------------------------------------------ #

    def _now(self) -> datetime:
        """Current time from the injected clock as an aware UTC datetime."""
        return _ensure_aware(self.clock()).astimezone(timezone.utc)

    def _zone(self) -> timezone | ZoneInfo:
        """``settings.timezone`` as a tzinfo, falling back to UTC when unknown."""
        try:
            return ZoneInfo(self.settings.timezone)
        except Exception:  # ZoneInfoNotFoundError, ValueError, missing tzdata ...
            return timezone.utc

    def _local_now(self) -> datetime:
        """The current time in ``settings.timezone`` (for quiet hours and day counters)."""
        return self._now().astimezone(self._zone())

    def _today(self) -> date:
        return self._local_now().date()

    # ------------------------------------------------------------------ #
    # Audit log
    # ------------------------------------------------------------------ #

    def _record(
        self,
        kind: ActionKind,
        *,
        offering_id: str = "",
        buyer_email: str = "",
        thread_id: str = "",
        message_id: str = "",
        details: str = "",
    ) -> ActionRecord:
        """Append an :class:`ActionRecord` to the store and return it."""
        action = ActionRecord(
            kind=kind,
            at=self._now(),
            offering_id=offering_id,
            buyer_email=buyer_email,
            thread_id=thread_id,
            message_id=message_id,
            details=details,
        )
        return self.store.add_action(action)

    def _error(self, report: RunReport, details: str, **fields: str) -> ActionRecord:
        """Record an ``ERROR`` action and remember it in ``report.errors``."""
        report.errors.append(details)
        return self._record(ActionKind.ERROR, details=details, **fields)

    @staticmethod
    def _base(inbound: InboundMessage, offering: Offering | None) -> dict[str, str]:
        """Audit-log fields shared by every action about one inbound message."""
        return dict(
            offering_id=offering.id if offering is not None else "",
            buyer_email=inbound.from_email,
            thread_id=inbound.thread_id,
            message_id=inbound.message_id,
        )

    # ------------------------------------------------------------------ #
    # Caps, quiet hours, recipient filtering
    # ------------------------------------------------------------------ #

    def _send_block_reason(self, count: int, *, owner_facing: bool = False) -> str | None:
        """Why ``count`` more recipients may not be mailed right now, or ``None``.

        Checks quiet hours (skipped for owner-facing mail), then
        ``Policy.max_sends_per_run``, then ``Policy.max_sends_per_day``.
        """
        policy = self.settings.policy
        if not owner_facing:
            local = self._local_now()
            if policy.in_quiet_hours(local.hour):
                return (
                    f"quiet hours {policy.quiet_hours_start}-{policy.quiet_hours_end} "
                    f"({self.settings.timezone}); local time {local.strftime('%H:%M')}"
                )
        if self._sent_this_run + count > policy.max_sends_per_run:
            return (
                f"per-run cap: {self._sent_this_run} sent this run + {count} "
                f"> max_sends_per_run={policy.max_sends_per_run}"
            )
        today = self._today()
        sent_today = self.store.sends_on(today)
        if sent_today + count > policy.max_sends_per_day:
            return (
                f"daily cap: {sent_today} sent on {today.isoformat()} + {count} "
                f"> max_sends_per_day={policy.max_sends_per_day}"
            )
        return None

    def _can_send(self, count: int = 1, *, owner_facing: bool = False) -> bool:
        """Whether ``count`` recipients may be mailed now (caps + quiet hours)."""
        return self._send_block_reason(count, owner_facing=owner_facing) is None

    def _count_sent(self, count: int) -> None:
        """Charge ``count`` recipients against the per-run and per-day counters."""
        self.store.add_sends(self._today(), count)
        self._sent_this_run += count

    def _internal_addresses(self) -> set[str]:
        """Owner / team addresses that are never filtered by the domain allowlist."""
        return {
            a.strip().lower()
            for a in (self.settings.sender_email, self.settings.escalation_email, self.settings.sales_team_email)
            if a and a.strip()
        }

    def _recipient_block_reason(self, email: str, *, check_status: bool = True) -> str | None:
        """Why ``email`` must not receive mail, or ``None`` if it may.

        Internal addresses are always allowed. Otherwise the domain allowlist
        is applied, then (``check_status``) the buyer's status: anything but
        ``ACTIVE`` is suppressed. Unknown addresses pass the status check.
        """
        email = email.strip().lower()
        if email in self._internal_addresses():
            return None
        if not self.settings.policy.recipient_allowed(email):
            return f"domain not allowed: {email.rsplit('@', 1)[-1]}"
        if check_status:
            buyer = self.store.get_buyer(email)
            if buyer is not None and buyer.status is not BuyerStatus.ACTIVE:
                return f"buyer {buyer.status.value}"
        return None

    def _partition_recipients(
        self, emails: list[str], *, check_status: bool = True
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Split ``emails`` into ``(allowed, [(skipped_email, reason), ...])``."""
        allowed: list[str] = []
        skipped: list[tuple[str, str]] = []
        for email in emails:
            reason = self._recipient_block_reason(email, check_status=check_status)
            if reason is None:
                allowed.append(email)
            else:
                skipped.append((email, reason))
        return allowed, skipped

    @staticmethod
    def _note_skip(report: RunReport, reason: str) -> None:
        """Count a filtered recipient in the report (suppressed buyer vs. other skip)."""
        if reason.startswith("buyer "):
            report.buyers_suppressed += 1
        else:
            report.skipped += 1

    def _label(self, name: str) -> str:
        prefix = self.settings.gmail_label_prefix.strip()
        return f"{prefix}/{name}" if prefix else name

    def _safe_label(self, thread_id: str, name: str) -> None:
        """Label a thread, ignoring transport errors (used on error paths)."""
        if not thread_id:
            return
        try:
            self.mailer.add_label(thread_id, self._label(name))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Message construction
    # ------------------------------------------------------------------ #

    def _outbound(
        self,
        rendered: RenderedEmail,
        *,
        to: list[str],
        bcc: list[str] | None = None,
        attachments: list[str] | None = None,
    ) -> OutboundMessage:
        """Wrap a rendered email as a fresh (non-reply) outbound message."""
        return OutboundMessage(
            subject=rendered.subject,
            body_text=rendered.text,
            body_html=rendered.html,
            to=list(to),
            bcc=list(bcc or []),
            reply_to=self.settings.reply_to,
            attachments=list(attachments or []),
        )

    def _reply_message(self, inbound: InboundMessage, rendered: RenderedEmail) -> OutboundMessage:
        """Wrap a rendered email as a reply in the inbound message's thread."""
        references = " ".join(
            part for part in (inbound.references.strip(), inbound.rfc_message_id.strip()) if part
        )
        return OutboundMessage(
            subject=_reply_subject(inbound, rendered),
            body_text=rendered.text,
            body_html=rendered.html,
            to=[inbound.from_email],
            reply_to=self.settings.reply_to,
            thread_id=inbound.thread_id,
            in_reply_to=inbound.rfc_message_id,
            references=references,
        )

    def _human_draft_body(self, inbound: InboundMessage, buyer: Buyer | None, note: str = "") -> str:
        """Body of a draft a human will finish: greeting, blank space, signature, quoted original."""
        blocks = [_greeting(buyer)]
        if note.strip():
            blocks.append(f"*** NOTE TO SENDER (delete before sending): {note.strip()} ***")
        blocks.extend(["", signature_block(self.settings), _quoted_original(inbound)])
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ #
    # Scheduling
    # ------------------------------------------------------------------ #

    def _campaign_id(self, offering_id: str, kind: CampaignKind) -> str:
        n = len(self.store.list_campaigns(offering_id=offering_id)) + 1
        while self.store.get_campaign(f"{offering_id}-{kind.value}-{n}") is not None:
            n += 1
        return f"{offering_id}-{kind.value}-{n}"

    def _render_single(
        self, campaign: Campaign, offering: Offering, email: str, now: datetime
    ) -> RenderedEmail:
        """Render a one-recipient campaign message (personal, follow-up, internal)."""
        if campaign.kind is CampaignKind.INTERNAL:
            return render_internal_sheet(offering, self.settings, when=now)
        buyer = self.store.get_buyer(email) or Buyer(email=email)
        if campaign.kind is CampaignKind.FOLLOW_UP:
            return render_follow_up(offering, buyer, self.settings, when=now)
        return render_offering_email(
            offering, self.settings, when=now, buyer=buyer, personal_note=campaign.personal_note
        )

    def _preview_subject(self, campaign: Campaign, offering: Offering, now: datetime) -> str:
        """The subject a campaign will go out with (recomputed at dispatch time)."""
        if campaign.kind in (CampaignKind.BLAST, CampaignKind.UPDATE):
            return blast_subject(offering, now, update=campaign.kind is CampaignKind.UPDATE)
        if campaign.kind is CampaignKind.INTERNAL:
            return internal_subject(offering, now)
        if campaign.kind is CampaignKind.PERSONAL:
            buyer = self.store.get_buyer(campaign.recipients[0]) or Buyer(email=campaign.recipients[0])
            return personal_subject(buyer, offering)
        return self._render_single(campaign, offering, campaign.recipients[0], now).subject

    def schedule_blast(
        self,
        offering_id: str,
        *,
        kind: CampaignKind = CampaignKind.BLAST,
        recipients: list[str] | None = None,
        personal_note: str = "",
        update: bool = False,
    ) -> Campaign:
        """Queue a campaign for the next :meth:`dispatch_campaigns`.

        Args:
            offering_id: The offering to send.
            kind: ``BLAST`` (Bcc chunks), ``PERSONAL`` (one buyer, ``NAME>>``
                subject), ``UPDATE`` (re-send with ``- 10/1 update``),
                ``FOLLOW_UP`` or ``INTERNAL`` (cost sheet to the sales team).
            recipients: Explicit addresses. ``None`` means every ``ACTIVE``
                buyer whose domain the policy allows. Ignored for ``INTERNAL``,
                which always goes to ``Settings.sales_team_email``.
            personal_note: One-line note placed first in a personal forward.
            update: Shorthand for ``kind=CampaignKind.UPDATE``.

        Returns:
            The stored ``SCHEDULED`` campaign with id ``"{offering_id}-{kind}-{n}"``.

        Raises:
            ValueError: Unknown offering; offering not ``DRAFT``/``ACTIVE``
                (buyer-facing kinds); no recipients; invalid address; a
                personal campaign without exactly one recipient; an internal
                sheet without ``Settings.sales_team_email``.
        """
        offering = self.store.get_offering(offering_id)
        if offering is None:
            raise ValueError(f"Unknown offering: {offering_id!r}")
        kind = CampaignKind(kind)
        if update and kind is CampaignKind.BLAST:
            kind = CampaignKind.UPDATE

        if kind is CampaignKind.INTERNAL:
            team = self.settings.sales_team_email.strip().lower()
            if not team:
                raise ValueError("Settings.sales_team_email is required to schedule an internal cost sheet.")
            recipients = [team]
        else:
            if offering.status not in (OfferingStatus.DRAFT, OfferingStatus.ACTIVE):
                raise ValueError(
                    f"Offering {offering_id!r} is {offering.status.value}; set it active before scheduling."
                )
            if recipients is None:
                recipients = [
                    b.email
                    for b in self.store.list_buyers(BuyerStatus.ACTIVE)
                    if self.settings.policy.recipient_allowed(b.email)
                ]
            else:
                recipients = _clean_recipients(recipients)
        if not recipients:
            raise ValueError(f"No recipients for offering {offering_id!r}.")
        if kind is CampaignKind.PERSONAL and len(recipients) != 1:
            raise ValueError("A personal campaign needs exactly one recipient.")

        if kind is not CampaignKind.INTERNAL and offering.status is OfferingStatus.DRAFT:
            self.store.set_offering_status(offering.id, OfferingStatus.ACTIVE)
            offering.status = OfferingStatus.ACTIVE

        now = self._now()
        campaign = Campaign(
            id=self._campaign_id(offering.id, kind),
            offering_id=offering.id,
            kind=kind,
            recipients=recipients,
            personal_note=personal_note.strip(),
            created_at=now,
        )
        campaign.subject = self._preview_subject(campaign, offering, now)
        self.store.add_campaign(campaign)
        return campaign

    def schedule_internal_sheet(self, offering_id: str) -> Campaign:
        """Queue the internal cost sheet for ``Settings.sales_team_email`` (``ValueError`` if unset)."""
        return self.schedule_blast(offering_id, kind=CampaignKind.INTERNAL)

    # ------------------------------------------------------------------ #
    # The loop
    # ------------------------------------------------------------------ #

    def run_once(self) -> RunReport:
        """One pass: expire → dispatch → process inbox → follow up.

        Never raises for per-item problems; they are recorded as ``ERROR``
        actions and listed in ``RunReport.errors``.
        """
        report = RunReport(started_at=self._now(), live=self.settings.policy.live)
        self._sent_this_run = 0
        for name in ("expire_offerings", "dispatch_campaigns", "process_inbox", "send_follow_ups"):
            try:
                getattr(self, name)(report)
            except Exception as exc:  # a step-level failure must not stop the pass
                self._error(report, f"{name} failed: {type(exc).__name__}: {exc}")
        report.finished_at = self._now()
        return report

    # ------------------------------------------------------------------ #
    # Expiry
    # ------------------------------------------------------------------ #

    def expire_offerings(self, report: RunReport) -> None:
        """Expire ``ACTIVE`` offerings past their deadline and cancel their scheduled campaigns."""
        now = self._now()
        ttl = timedelta(days=self.settings.policy.offering_ttl_days)
        for offering in self.store.list_offerings(OfferingStatus.ACTIVE):
            try:
                deadline = offering.expires_at or (offering.created_at + ttl)
                if _ensure_aware(deadline) >= now:
                    continue
                self.store.set_offering_status(offering.id, OfferingStatus.EXPIRED)
                cancelled = 0
                for campaign in self.store.list_campaigns(offering_id=offering.id, status=CampaignStatus.SCHEDULED):
                    campaign.status = CampaignStatus.CANCELLED
                    campaign.error = "offering expired"
                    self.store.update_campaign(campaign)
                    cancelled += 1
                report.offerings_expired += 1
                self._record(
                    ActionKind.OFFERING_EXPIRED,
                    offering_id=offering.id,
                    details=f"expired (deadline {deadline.isoformat()}); {cancelled} scheduled campaign(s) cancelled",
                )
            except Exception as exc:
                self._error(report, f"expire {offering.id}: {type(exc).__name__}: {exc}", offering_id=offering.id)

    # ------------------------------------------------------------------ #
    # Campaign dispatch
    # ------------------------------------------------------------------ #

    def dispatch_campaigns(self, report: RunReport) -> None:
        """Send every ``SCHEDULED`` campaign whose offering is still sendable.

        A campaign whose offering is missing or no longer ``ACTIVE`` (``DRAFT``
        is also fine for an internal sheet) is ``CANCELLED``. A campaign that
        hits a cap or quiet hours stays ``SCHEDULED`` and resumes with the
        recipients not yet contacted on the next run; one whose transport
        fails is ``FAILED``.
        """
        for campaign in self.store.list_campaigns(status=CampaignStatus.SCHEDULED):
            try:
                self._dispatch_campaign(campaign, report)
            except Exception as exc:
                campaign.status = CampaignStatus.FAILED
                campaign.error = f"{type(exc).__name__}: {exc}"
                try:
                    self.store.update_campaign(campaign)
                except Exception:
                    pass
                self._error(report, f"campaign {campaign.id}: {campaign.error}", offering_id=campaign.offering_id)

    def _cancel_campaign(self, campaign: Campaign, reason: str) -> None:
        campaign.status = CampaignStatus.CANCELLED
        campaign.error = reason
        self.store.update_campaign(campaign)
        self._record(
            ActionKind.IGNORED,
            offering_id=campaign.offering_id,
            details=f"campaign {campaign.id} cancelled: {reason}",
        )

    def _log_skipped(
        self, report: RunReport, skipped: list[tuple[str, str]], offering_id: str, campaign_id: str
    ) -> None:
        if not skipped:
            return
        for _email, reason in skipped:
            self._note_skip(report, reason)
        shown = ", ".join(f"{email} ({reason})" for email, reason in skipped[:10])
        if len(skipped) > 10:
            shown += f", ... {len(skipped) - 10} more"
        self._record(
            ActionKind.IGNORED,
            offering_id=offering_id,
            details=f"campaign {campaign_id}: {len(skipped)} recipient(s) skipped: {shown}",
        )

    def _dispatch_campaign(self, campaign: Campaign, report: RunReport) -> None:
        offering = self.store.get_offering(campaign.offering_id)
        sendable = {OfferingStatus.ACTIVE}
        if campaign.kind is CampaignKind.INTERNAL:
            sendable.add(OfferingStatus.DRAFT)
        if offering is None:
            self._cancel_campaign(campaign, "offering not found")
            return
        if offering.status not in sendable:
            self._cancel_campaign(campaign, f"offering is {offering.status.value}")
            return

        is_internal = campaign.kind is CampaignKind.INTERNAL
        contact_kind = ContactKind.FOLLOW_UP if campaign.kind is CampaignKind.FOLLOW_UP else ContactKind.INITIAL
        recipients, skipped = self._partition_recipients(campaign.recipients, check_status=not is_internal)
        self._log_skipped(report, skipped, offering.id, campaign.id)
        if not recipients:
            self._cancel_campaign(campaign, "no eligible recipients")
            return
        # A campaign deferred by a cap resumes where it stopped: recipients that
        # already have a contact for this campaign were mailed in an earlier run.
        already: set[str] = set()
        if not is_internal:
            already = {
                c.buyer_email
                for c in self.store.contacts(offering.id, kind=contact_kind)
                if c.campaign_id == campaign.id
            }
        recipients = [r for r in recipients if r not in already]
        now = self._now()
        if not recipients:
            self._finish_campaign(campaign, now, report)
            return

        batches: list[tuple[list[str], OutboundMessage]] = []
        if campaign.kind in (CampaignKind.BLAST, CampaignKind.UPDATE):
            rendered = render_offering_email(
                offering, self.settings, when=now, update=campaign.kind is CampaignKind.UPDATE
            )
            size = self.settings.policy.bcc_chunk_size
            for start in range(0, len(recipients), size):
                chunk = recipients[start : start + size]
                message = self._outbound(
                    rendered, to=[self.settings.sender_email], bcc=chunk, attachments=offering.attachments
                )
                batches.append((chunk, message))
        else:
            for email in recipients:
                rendered = self._render_single(campaign, offering, email, now)
                message = self._outbound(rendered, to=[email], attachments=offering.attachments)
                batches.append(([email], message))

        campaign.subject = batches[0][1].subject
        action_kind = _CAMPAIGN_ACTION[campaign.kind]
        sent: list[str] = []
        total = len(batches)
        for index, (chunk, message) in enumerate(batches, start=1):
            reason = self._send_block_reason(len(chunk))
            if reason is not None:
                remaining = len(recipients) - len(sent)
                campaign.status = CampaignStatus.SCHEDULED
                campaign.error = f"deferred: {reason}"
                self.store.update_campaign(campaign)
                self._record(
                    ActionKind.IGNORED,
                    offering_id=offering.id,
                    details=(
                        f"campaign {campaign.id} deferred before chunk {index}/{total} "
                        f"({len(chunk)} recipient(s); {remaining} still to send): {reason}"
                    ),
                )
                return
            if campaign.status is not CampaignStatus.SENDING:
                campaign.status = CampaignStatus.SENDING
                self.store.update_campaign(campaign)

            result = self.mailer.send(message)
            single_buyer = chunk[0] if len(chunk) == 1 and not is_internal else ""
            self.store.map_thread(result.thread_id, offering.id, single_buyer)
            if not is_internal:
                for email in chunk:
                    self.store.record_contact(
                        Contact(
                            offering_id=offering.id,
                            buyer_email=email,
                            kind=contact_kind,
                            at=now,
                            thread_id=result.thread_id,
                            message_id=result.message_id,
                            campaign_id=campaign.id,
                        )
                    )
            self._count_sent(len(chunk))
            report.messages_sent += len(chunk)
            if campaign.kind is CampaignKind.FOLLOW_UP:
                report.follow_ups_sent += len(chunk)
            campaign.thread_ids.append(result.thread_id)
            sent.extend(chunk)
            self._record(
                action_kind,
                offering_id=offering.id,
                buyer_email=single_buyer,
                thread_id=result.thread_id,
                message_id=result.message_id,
                details=(
                    f"campaign {campaign.id} ({campaign.kind.value}) chunk {index}/{total}: "
                    f"{len(chunk)} recipient(s); subject {message.subject!r}"
                ),
            )

        self._finish_campaign(campaign, now, report)

    def _finish_campaign(self, campaign: Campaign, now: datetime, report: RunReport) -> None:
        campaign.status = CampaignStatus.SENT
        campaign.sent_at = now
        campaign.error = ""
        self.store.update_campaign(campaign)
        report.campaigns_sent += 1

    # ------------------------------------------------------------------ #
    # Inbox
    # ------------------------------------------------------------------ #

    def _inbox_since(self, now: datetime) -> datetime:
        raw = self.store.get_state(LAST_INBOX_POLL_KEY)
        if raw:
            try:
                return _ensure_aware(datetime.fromisoformat(raw))
            except ValueError:
                pass
        return now - timedelta(days=self.settings.policy.inbox_lookback_days)

    def _resolve_offering(self, inbound: InboundMessage) -> Offering | None:
        """The offering a message is about: thread map first, else the longest title in the subject."""
        if inbound.thread_id:
            mapped = self.store.offering_for_thread(inbound.thread_id)
            if mapped:
                offering = self.store.get_offering(mapped)
                if offering is not None:
                    return offering
        subject = _normalise_subject(inbound.subject)
        if not subject:
            return None
        best: Offering | None = None
        best_key: tuple[int, int] = (0, 0)
        for offering in self.store.list_offerings():
            title = _WS_RE.sub(" ", offering.title).strip().lower()
            if title and title in subject:
                key = (len(title), 1 if offering.status is OfferingStatus.ACTIVE else 0)
                if best is None or key > best_key:
                    best, best_key = offering, key
        return best

    def process_inbox(self, report: RunReport) -> None:
        """Fetch new mail since the last poll and route each message once.

        Skipped without an action: already-processed ids, mail from the
        sender, and mail that matches no offering *and* comes from an unknown
        address (the owner's mailbox holds plenty of unrelated mail). A known
        buyer writing outside any offering thread is still handled so that
        unsubscribe requests and firm offers are never missed.
        """
        now = self._now()
        since = self._inbox_since(now)
        try:
            messages = self.mailer.fetch_inbound(since)
        except Exception as exc:
            self._error(report, f"fetch_inbound failed: {type(exc).__name__}: {exc}")
            return

        latest = since
        for inbound in messages:
            latest = max(latest, _ensure_aware(inbound.date))
            try:
                self._process_one(inbound, report)
            except Exception as exc:
                self._error(
                    report,
                    f"inbound {inbound.message_id} from {inbound.from_email}: {type(exc).__name__}: {exc}",
                    thread_id=inbound.thread_id,
                    message_id=inbound.message_id,
                )
                try:
                    self.store.mark_processed(inbound.message_id, self._now())
                except Exception:
                    pass
                self._safe_label(inbound.thread_id, NEEDS_REPLY_LABEL)
        if latest > since:
            self.store.set_state(LAST_INBOX_POLL_KEY, latest.isoformat())

    def _process_one(self, inbound: InboundMessage, report: RunReport) -> None:
        if self.store.is_processed(inbound.message_id):
            report.skipped += 1
            return
        if inbound.from_email == self.settings.sender_email:
            report.skipped += 1
            return
        offering = self._resolve_offering(inbound)
        if offering is None and self.store.get_buyer(inbound.from_email) is None:
            report.skipped += 1
            return
        self.handle_inbound(inbound, offering, report)
        self.store.mark_processed(inbound.message_id, self._now())

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #

    def _classify(self, inbound: InboundMessage, offering: Offering | None) -> Classification:
        """Rule pre-checks (bounce, auto-reply, unsubscribe), then the configured classifier."""
        if is_bounce(inbound):
            return Classification(ReplyIntent.BOUNCE, confidence=0.95, summary="delivery failure report")
        if is_auto_reply(inbound):
            return Classification(ReplyIntent.OUT_OF_OFFICE, confidence=0.9, summary="automatic reply")
        text = strip_quoted_text(inbound.body_text or "")
        subject = _SUBJECT_PREFIX_RE.sub("", inbound.subject or "")
        if is_unsubscribe(text) or is_unsubscribe(subject):
            return Classification(ReplyIntent.UNSUBSCRIBE, confidence=0.9, summary="asked to be removed")
        return self.classifier.classify(inbound, offering)

    def _buyer_for(self, inbound: InboundMessage) -> Buyer:
        """The stored buyer for the sender, or an implicit one created from the message."""
        buyer = self.store.get_buyer(inbound.from_email)
        if buyer is None:
            buyer = Buyer(email=inbound.from_email, name=inbound.from_name.strip())
            self.store.upsert_buyer(buyer)
        return buyer

    def handle_inbound(
        self, inbound: InboundMessage, offering: Offering | None, report: RunReport
    ) -> ActionRecord:
        """Classify one inbound message and act on it (see the routing table in the design doc).

        Returns:
            The primary :class:`ActionRecord` describing what was done.
        """
        classification = self._classify(inbound, offering)
        intent = classification.intent
        base = self._base(inbound, offering)

        if intent is ReplyIntent.BOUNCE:
            action = self._handle_bounce(inbound, offering, report)
        elif intent is ReplyIntent.OUT_OF_OFFICE:
            action = self._record(
                ActionKind.IGNORED, details=f"auto-reply ignored: {classification.summary}", **base
            )
        else:
            buyer = self._buyer_for(inbound)
            if offering is not None:
                self.store.record_reply(offering.id, buyer.email, inbound.message_id, intent, inbound.date)
                if inbound.thread_id:
                    self.store.map_thread(inbound.thread_id, offering.id, buyer.email)
            if intent is ReplyIntent.UNSUBSCRIBE:
                action = self._handle_unsubscribe(inbound, offering, buyer, classification, report)
            elif intent is ReplyIntent.FIRM_OFFER:
                action = self._handle_firm_offer(inbound, offering, buyer, classification, report)
            elif intent is ReplyIntent.DELIVERED_PRICE_REQUEST:
                action = self._handle_price_request(inbound, offering, buyer, classification, report)
            elif intent is ReplyIntent.INTERESTED:
                action = self._handle_interested(inbound, offering, buyer, classification, report)
            elif intent is ReplyIntent.NOT_INTERESTED:
                action = self._handle_not_interested(inbound, offering, buyer, classification)
            else:  # QUESTION / UNKNOWN
                action = self._draft_for_human(
                    inbound, offering, buyer, report, reason=f"{intent.value}: {classification.summary}"
                )
        report.replies_processed += 1
        return action

    def _handle_bounce(
        self, inbound: InboundMessage, offering: Offering | None, report: RunReport
    ) -> ActionRecord:
        base = self._base(inbound, offering)
        haystack = " ".join(
            (inbound.body_text or "", inbound.headers.get("x-failed-recipients", ""), inbound.subject or "")
        )
        candidates: list[str] = []
        for match in _EMAIL_IN_TEXT_RE.finditer(haystack):
            email = match.group(0).lower().rstrip(".")
            if email != self.settings.sender_email and email not in candidates:
                candidates.append(email)
        first: ActionRecord | None = None
        for email in candidates:
            buyer = self.store.get_buyer(email)
            if buyer is None:
                continue
            if buyer.status is not BuyerStatus.BOUNCED:
                self.store.set_buyer_status(email, BuyerStatus.BOUNCED)
                report.buyers_suppressed += 1
            action = self._record(
                ActionKind.BUYER_BOUNCED,
                details=f"bounce from {inbound.from_email}: {email} marked bounced",
                **{**base, "buyer_email": email},
            )
            first = first or action
        if first is None:
            first = self._record(ActionKind.IGNORED, details="bounce ignored: no known buyer address in it", **base)
        return first

    def _handle_unsubscribe(
        self,
        inbound: InboundMessage,
        offering: Offering | None,
        buyer: Buyer,
        classification: Classification,
        report: RunReport,
    ) -> ActionRecord:
        if buyer.status is not BuyerStatus.UNSUBSCRIBED:
            self.store.set_buyer_status(buyer.email, BuyerStatus.UNSUBSCRIBED)
            report.buyers_suppressed += 1
        return self._record(
            ActionKind.BUYER_UNSUBSCRIBED,
            details=f"unsubscribed on request: {classification.summary}",
            **self._base(inbound, offering),
        )

    def _send_owner(self, message: OutboundMessage, report: RunReport) -> SendResult:
        """Send owner-facing mail: always sent (never drafted), caps apply, quiet hours do not."""
        reason = self._send_block_reason(len(message.all_recipients), owner_facing=True)
        if reason is not None:
            raise EngineError(f"owner-facing send blocked: {reason}")
        result = self.mailer.send(message)
        self._count_sent(len(message.all_recipients))
        report.messages_sent += len(message.all_recipients)
        return result

    def _handle_firm_offer(
        self,
        inbound: InboundMessage,
        offering: Offering | None,
        buyer: Buyer,
        classification: Classification,
        report: RunReport,
    ) -> ActionRecord:
        base = self._base(inbound, offering)
        # Label first so the thread is flagged for the owner even if the send fails.
        if inbound.thread_id:
            self.mailer.add_label(inbound.thread_id, self._label(OFFER_LABEL))
        rendered = render_offer_escalation(
            offering, inbound, classification, self.settings, thread_url=self.mailer.thread_url(inbound.thread_id)
        )
        result = self._send_owner(self._outbound(rendered, to=[self.settings.escalation_email]), report)
        report.offers_escalated += 1
        price = f" offer_price={classification.offer_price}" if classification.offer_price is not None else ""
        action = self._record(
            ActionKind.OFFER_ESCALATED,
            details=(
                f"firm offer escalated to {self.settings.escalation_email} "
                f"(message {result.message_id}); NOT accepted;{price} {classification.summary}"
            ).strip(),
            **base,
        )
        if self.settings.policy.acknowledge_offers and offering is not None:
            self._reply(
                inbound,
                render_offer_acknowledgement(offering, buyer, self.settings),
                offering=offering,
                buyer=buyer,
                report=report,
            )
        return action

    def _learn_destination(self, buyer: Buyer, classification: Classification) -> None:
        """Remember a ZIP / city the buyer just told us."""
        changed = False
        postal = (classification.postal_code or "").strip()
        if postal and postal != buyer.postal_code:
            buyer.postal_code = postal
            changed = True
        place = (classification.destination or "").strip()
        if place and not buyer.city_state:
            buyer.city_state = place
            changed = True
        if changed:
            self.store.upsert_buyer(buyer)

    def _handle_price_request(
        self,
        inbound: InboundMessage,
        offering: Offering | None,
        buyer: Buyer,
        classification: Classification,
        report: RunReport,
    ) -> ActionRecord:
        if offering is None:
            return self._draft_for_human(
                inbound, None, buyer, report, reason="delivered price request, no matching offering"
            )
        self._learn_destination(buyer, classification)
        destination = (classification.postal_code or classification.destination or buyer.destination or "").strip()
        if not destination:
            return self._reply(
                inbound,
                render_zip_request_reply(offering, buyer, self.settings),
                offering=offering,
                buyer=buyer,
                report=report,
            )
        truckloads = classification.truckloads or 1.0
        try:
            quote = self.quote(offering, destination, truckloads)
        except Exception as exc:
            detail = f"delivered quote to {destination} ({truckloads:g} truckload(s)) failed: {type(exc).__name__}: {exc}"
            error = self._error(report, detail, **self._base(inbound, offering))
            self._draft_for_human(inbound, offering, buyer, report, reason="delivered quote failed", note=detail)
            return error
        return self._reply(
            inbound,
            render_delivered_quote_reply(offering, quote, buyer, self.settings),
            offering=offering,
            buyer=buyer,
            report=report,
            contact_kind=ContactKind.QUOTE,
            sent_action=ActionKind.QUOTE_SENT,
        )

    def _handle_interested(
        self,
        inbound: InboundMessage,
        offering: Offering | None,
        buyer: Buyer,
        classification: Classification,
        report: RunReport,
    ) -> ActionRecord:
        if offering is None:
            return self._draft_for_human(
                inbound, None, buyer, report, reason=f"interested, no matching offering: {classification.summary}"
            )
        return self._reply(
            inbound,
            render_interested_reply(offering, buyer, self.settings),
            offering=offering,
            buyer=buyer,
            report=report,
        )

    def _handle_not_interested(
        self,
        inbound: InboundMessage,
        offering: Offering | None,
        buyer: Buyer,
        classification: Classification,
    ) -> ActionRecord:
        tag = f"declined:{offering.id if offering is not None else 'unknown'}"
        if tag not in buyer.tags:
            buyer.tags.append(tag)
            self.store.upsert_buyer(buyer)
        return self._record(
            ActionKind.BUYER_DECLINED,
            details=f"tagged {tag}: {classification.summary}",
            **self._base(inbound, offering),
        )

    def _contact(
        self,
        offering: Offering | None,
        buyer: Buyer,
        kind: ContactKind,
        inbound: InboundMessage,
        result: SendResult,
    ) -> None:
        if offering is None:
            return
        self.store.record_contact(
            Contact(
                offering_id=offering.id,
                buyer_email=buyer.email,
                kind=kind,
                at=self._now(),
                thread_id=result.thread_id or inbound.thread_id,
                message_id=result.message_id,
            )
        )

    def _reply(
        self,
        inbound: InboundMessage,
        rendered: RenderedEmail,
        *,
        offering: Offering | None,
        buyer: Buyer,
        report: RunReport,
        contact_kind: ContactKind = ContactKind.REPLY,
        sent_action: ActionKind = ActionKind.REPLY_SENT,
    ) -> ActionRecord:
        """Answer ``inbound`` with ``rendered`` according to ``Policy.auto_reply_mode``.

        ``"send"`` mails the reply in the thread (``Re:`` subject, ``In-Reply-To``
        / ``References``) and records a ``QUOTE``/``REPLY`` contact; ``"draft"``
        creates a Gmail draft and records a ``DRAFT`` contact; ``"off"`` does
        nothing but log ``IGNORED``. Recipient filtering (status + domains)
        and :meth:`_can_send` apply; a send blocked by a cap or quiet hours is
        drafted instead so the answer is not lost.
        """
        base = self._base(inbound, offering)
        block = self._recipient_block_reason(inbound.from_email)
        if block is not None:
            self._note_skip(report, block)
            return self._record(ActionKind.IGNORED, details=f"reply skipped: {block}", **base)
        mode = self.settings.policy.auto_reply_mode
        if mode == "off":
            return self._record(
                ActionKind.IGNORED, details=f"auto_reply_mode=off; not answered: {rendered.subject}", **base
            )
        message = self._reply_message(inbound, rendered)
        note = ""
        if mode == "send":
            reason = self._send_block_reason(1)
            if reason is None:
                result = self.mailer.send(message)
                self._count_sent(1)
                report.messages_sent += 1
                if sent_action is ActionKind.QUOTE_SENT:
                    report.quotes_sent += 1
                self._contact(offering, buyer, contact_kind, inbound, result)
                return self._record(sent_action, details=f"sent {message.subject!r}", **base)
            note = f" (send blocked: {reason}; drafted instead)"
        result = self.mailer.create_draft(message)
        report.drafts_created += 1
        self._contact(offering, buyer, ContactKind.DRAFT, inbound, result)
        return self._record(ActionKind.DRAFT_CREATED, details=f"draft {message.subject!r}{note}", **base)

    def _draft_for_human(
        self,
        inbound: InboundMessage,
        offering: Offering | None,
        buyer: Buyer,
        report: RunReport,
        *,
        reason: str,
        note: str = "",
    ) -> ActionRecord:
        """Draft a reply with the original quoted for a human to finish; label the thread."""
        base = self._base(inbound, offering)
        block = self._recipient_block_reason(inbound.from_email)
        if block is not None:
            self._note_skip(report, block)
            return self._record(ActionKind.IGNORED, details=f"needs human reply ({reason}); skipped: {block}", **base)
        if self.settings.policy.auto_reply_mode == "off":
            if inbound.thread_id:
                self.mailer.add_label(inbound.thread_id, self._label(NEEDS_REPLY_LABEL))
            return self._record(
                ActionKind.IGNORED, details=f"needs human reply ({reason}); auto_reply_mode=off", **base
            )
        body = self._human_draft_body(inbound, buyer, note)
        rendered = RenderedEmail(subject=inbound.subject or "Re: your message", text=body, html=text_to_html(body))
        message = self._reply_message(inbound, rendered)
        result = self.mailer.create_draft(message)
        report.drafts_created += 1
        self._contact(offering, buyer, ContactKind.DRAFT, inbound, result)
        if inbound.thread_id:
            self.mailer.add_label(inbound.thread_id, self._label(NEEDS_REPLY_LABEL))
        return self._record(
            ActionKind.DRAFT_CREATED, details=f"draft for human reply ({reason}): {message.subject!r}", **base
        )

    # ------------------------------------------------------------------ #
    # Follow-ups
    # ------------------------------------------------------------------ #

    def send_follow_ups(self, report: RunReport) -> None:
        """Nudge buyers who never replied to an ``ACTIVE`` offering.

        Due buyers come from ``Store.buyers_due_follow_up``. The nudge is sent
        when ``Policy.auto_send_follow_ups`` is set, drafted otherwise; either
        way a ``FOLLOW_UP`` contact is recorded so the buyer is not nudged
        again until the next window. A cap or quiet hours stops the rest of
        this run's follow-ups.
        """
        policy = self.settings.policy
        now = self._now()
        before = now - timedelta(days=policy.follow_up_after_days)
        for offering in self.store.list_offerings(OfferingStatus.ACTIVE):
            for email in self.store.buyers_due_follow_up(offering.id, before, policy.max_follow_ups):
                try:
                    if not self._follow_up(offering, email, now, report):
                        return
                except Exception as exc:
                    self._error(
                        report,
                        f"follow-up to {email} for {offering.id}: {type(exc).__name__}: {exc}",
                        offering_id=offering.id,
                        buyer_email=email,
                    )

    def _follow_up(self, offering: Offering, email: str, now: datetime, report: RunReport) -> bool:
        """Send or draft one follow-up. Returns ``False`` when a cap stops further sends."""
        block = self._recipient_block_reason(email)
        if block is not None:
            self._note_skip(report, block)
            self._record(
                ActionKind.IGNORED, offering_id=offering.id, buyer_email=email, details=f"follow-up skipped: {block}"
            )
            return True
        buyer = self.store.get_buyer(email) or Buyer(email=email)
        rendered = render_follow_up(offering, buyer, self.settings, when=now)
        message = self._outbound(rendered, to=[email])
        if self.settings.policy.auto_send_follow_ups:
            reason = self._send_block_reason(1)
            if reason is not None:
                self._record(
                    ActionKind.IGNORED, offering_id=offering.id, details=f"follow-ups deferred: {reason}"
                )
                return False
            result = self.mailer.send(message)
            self._count_sent(1)
            report.messages_sent += 1
            report.follow_ups_sent += 1
            self.store.map_thread(result.thread_id, offering.id, email)
            action_kind, verb = ActionKind.FOLLOW_UP_SENT, "sent"
        else:
            result = self.mailer.create_draft(message)
            report.drafts_created += 1
            action_kind, verb = ActionKind.DRAFT_CREATED, "drafted"
        self.store.record_contact(
            Contact(
                offering_id=offering.id,
                buyer_email=email,
                kind=ContactKind.FOLLOW_UP,
                at=now,
                thread_id=result.thread_id,
                message_id=result.message_id,
            )
        )
        self._record(
            action_kind,
            offering_id=offering.id,
            buyer_email=email,
            thread_id=result.thread_id,
            message_id=result.message_id,
            details=f"follow-up {verb}: {rendered.subject!r}",
        )
        return True

    # ------------------------------------------------------------------ #
    # Quotes and digests
    # ------------------------------------------------------------------ #

    def quote(self, offering: Offering, destination: str, truckloads: float = 1.0) -> DeliveredQuote:
        """Delivered quote for ``offering`` to a ZIP or ``"City, ST"``.

        Raises:
            EngineError: If no distance provider is configured.
            ValueError: From ``pricing`` (empty destination, missing
                ``units_per_truckload`` ...); provider errors propagate.
        """
        if self.distance_provider is None:
            raise EngineError(
                "No distance provider configured: set OFFERINGS_GOOGLE_MAPS_API_KEY (google_maps_api_key) to quote delivered freight."
            )
        return quote_delivered(
            offering,
            normalize_destination(destination, None),
            truckloads=truckloads,
            rate_per_mile=self.settings.rate_per_mile,
            distance_provider=self.distance_provider,
            margin_per_truckload=self.settings.freight_margin_per_truckload,
            now=self._now(),
        )

    def digest(self, *, since: datetime) -> RenderedEmail:
        """Owner digest of everything recorded since ``since``."""
        since = _ensure_aware(since)
        actions = self.store.list_actions(since=since)
        pending = sum(1 for a in actions if a.kind is ActionKind.DRAFT_CREATED)
        return render_digest(actions, self.settings, since=since, until=self._now(), pending_drafts=pending)

    def send_digest(self, *, since: datetime) -> SendResult:
        """Mail the digest to ``Settings.escalation_email``.

        Always sent (never drafted) because it is owner-facing; the send caps
        apply but quiet hours do not.

        Raises:
            EngineError: If a send cap is reached.
        """
        rendered = self.digest(since=since)
        message = self._outbound(rendered, to=[self.settings.escalation_email])
        reason = self._send_block_reason(1, owner_facing=True)
        if reason is not None:
            raise EngineError(f"digest not sent: {reason}")
        result = self.mailer.send(message)
        self._count_sent(1)
        return result
