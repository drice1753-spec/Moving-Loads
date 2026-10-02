"""Mail transport: Gmail REST API and a dry-run outbox.

Two implementations of the :class:`Mailer` protocol live here:

* :class:`DryRunMailer` never touches the network. It records every message
  in memory and, when given an outbox directory, writes each one as a
  numbered ``.eml`` file so the owner can open it in a mail client and check
  what *would* have gone out. It is the default (``Policy.live=False``).
* :class:`GmailMailer` talks to the Gmail REST API with a ``requests.Session``
  and an OAuth refresh token. Access tokens are cached and refreshed shortly
  before they expire; a single ``401`` is retried once after a forced refresh.

The pure helpers :func:`build_mime` and :func:`parse_gmail_message` convert
between the package's :class:`~email_offerings.models.OutboundMessage` /
:class:`~email_offerings.models.InboundMessage` records and MIME / Gmail
``users.messages`` resources, so the two mailers share one representation.
"""

from __future__ import annotations

import base64
import email.message
import email.utils
import html as html_lib
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

import requests

from email_offerings.config import Settings
from email_offerings.models import InboundMessage, OutboundMessage, SendResult, utcnow

__all__ = [
    "DryRunMailer",
    "GmailMailer",
    "Mailer",
    "MailerError",
    "build_mailer",
    "build_mime",
    "html_to_text",
    "parse_gmail_message",
]


class MailerError(RuntimeError):
    """Raised when the mail provider rejects a request or an attachment is missing."""


@runtime_checkable
class Mailer(Protocol):
    """Transport used by the engine. Both mailers implement this interface."""

    def send(self, message: OutboundMessage) -> SendResult: ...

    def create_draft(self, message: OutboundMessage) -> SendResult: ...

    def fetch_inbound(self, since: datetime, *, max_results: int = 200) -> list[InboundMessage]:
        """Messages received after ``since``, not from the sender, not drafts."""
        ...

    def fetch_thread(self, thread_id: str) -> list[InboundMessage]: ...

    def add_label(self, thread_id: str, label: str) -> None:
        """Label a thread, creating the label if missing (no-op in dry run)."""
        ...

    def thread_url(self, thread_id: str) -> str: ...


# --------------------------------------------------------------------------- #
# MIME construction
# --------------------------------------------------------------------------- #


def _ensure_aware(when: datetime) -> datetime:
    """Treat a naive datetime as UTC; return aware datetimes unchanged."""
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def build_mime(
    message: OutboundMessage,
    *,
    sender_email: str,
    sender_name: str = "",
) -> email.message.EmailMessage:
    """Build a MIME message from an :class:`OutboundMessage`.

    The ``Bcc`` header *is* written: Gmail's ``messages.send`` endpoint reads
    it to pick the envelope recipients and strips it from the delivered copy.

    Args:
        message: The message to render.
        sender_email: Address for the ``From`` header (omitted when empty).
        sender_name: Optional display name (``"Name <addr>"``).

    Returns:
        An ``EmailMessage`` with text body, optional HTML alternative and
        file attachments (MIME type guessed from the file name).

    Raises:
        MailerError: If an attachment path does not point to a file.
    """
    msg = email.message.EmailMessage()
    if sender_email:
        msg["From"] = email.utils.formataddr((sender_name, sender_email))
    if message.to:
        msg["To"] = ", ".join(message.to)
    if message.cc:
        msg["Cc"] = ", ".join(message.cc)
    if message.bcc:
        msg["Bcc"] = ", ".join(message.bcc)
    msg["Subject"] = message.subject
    if message.reply_to:
        msg["Reply-To"] = message.reply_to
    if message.in_reply_to:
        msg["In-Reply-To"] = message.in_reply_to
    if message.references:
        msg["References"] = message.references
    msg["Date"] = email.utils.format_datetime(utcnow())
    domain = sender_email.rsplit("@", 1)[-1] if "@" in sender_email else None
    msg["Message-ID"] = email.utils.make_msgid(domain=domain)
    for name, value in message.headers.items():
        if name in msg:
            msg.replace_header(name, value)
        else:
            msg[name] = value

    msg.set_content(message.body_text)
    if message.body_html:
        msg.add_alternative(message.body_html, subtype="html")

    for raw_path in message.attachments:
        path = Path(raw_path)
        if not path.is_file():
            raise MailerError(f"Attachment not found: {raw_path}")
        ctype, encoding = mimetypes.guess_type(path.name)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def _encode_raw(mime: email.message.EmailMessage) -> str:
    """Base64url-encode a MIME message the way Gmail's ``raw`` field expects."""
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")


# --------------------------------------------------------------------------- #
# Gmail resource parsing
# --------------------------------------------------------------------------- #

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_P_OPEN_RE = re.compile(r"<\s*(p|div|tr|li|h[1-6]|blockquote)\b[^>]*>", re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r"<\s*/\s*(p|div|tr|li|h[1-6]|blockquote|table|ul|ol)\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v\xa0]+")
_CHARSET_RE = re.compile(r"charset\s*=\s*\"?([\w.:\-]+)\"?", re.IGNORECASE)


def html_to_text(html: str) -> str:
    """Reduce an HTML body to readable plain text.

    ``<br>`` and block-level tags (``<p>``, ``<div>``, list items, headings,
    table rows) become newlines, every other tag is dropped, ``<script>`` and
    ``<style>`` blocks are removed with their content, entities are unescaped
    and whitespace is collapsed (at most one blank line in a row).
    """
    text = _SCRIPT_STYLE_RE.sub("", html)
    text = _BR_RE.sub("\n", text)
    text = _P_OPEN_RE.sub("\n", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_lib.unescape(text)
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _b64url_decode(data: str) -> bytes:
    """Decode base64url text that may lack padding (as Gmail returns it)."""
    cleaned = data.strip()
    cleaned += "=" * (-len(cleaned) % 4)
    return base64.urlsafe_b64decode(cleaned)


def _header_map(raw_headers: Any) -> dict[str, str]:
    """Turn Gmail's ``[{"name", "value"}]`` list into a lower-cased dict (first wins)."""
    headers: dict[str, str] = {}
    for item in raw_headers or []:
        name = str(item.get("name", "")).strip().lower()
        if name and name not in headers:
            headers[name] = str(item.get("value", ""))
    return headers


def _walk_parts(part: dict) -> Iterator[dict]:
    """Yield ``part`` and every nested part, depth first, in document order."""
    yield part
    for child in part.get("parts") or []:
        yield from _walk_parts(child)


def _decode_part(part: dict) -> str:
    """Decode a leaf part's body using the charset from its ``Content-Type``."""
    data = (part.get("body") or {}).get("data") or ""
    raw = _b64url_decode(data)
    content_type = _header_map(part.get("headers")).get("content-type", "")
    match = _CHARSET_RE.search(content_type)
    charset = match.group(1) if match else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    """Return the first ``text/plain`` body, else the first ``text/html`` as text."""
    text_part: dict | None = None
    html_part: dict | None = None
    for part in _walk_parts(payload):
        if part.get("filename") or not (part.get("body") or {}).get("data"):
            continue  # attachment or container part
        mime = str(part.get("mimeType", "")).lower()
        if mime == "text/plain" and text_part is None:
            text_part = part
        elif mime == "text/html" and html_part is None:
            html_part = part
    if text_part is not None:
        return _decode_part(text_part).replace("\r\n", "\n")
    if html_part is not None:
        return html_to_text(_decode_part(html_part))
    return ""


def _message_date(resource: dict, headers: dict[str, str]) -> datetime:
    """``internalDate`` (epoch ms) → aware UTC; fall back to the ``Date`` header."""
    internal = resource.get("internalDate")
    if internal not in (None, ""):
        return datetime.fromtimestamp(int(internal) / 1000.0, tz=timezone.utc)
    header = headers.get("date", "")
    if header:
        try:
            parsed = email.utils.parsedate_to_datetime(header)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            return _ensure_aware(parsed).astimezone(timezone.utc)
    return utcnow()


def parse_gmail_message(resource: dict) -> InboundMessage:
    """Convert a ``users.messages.get(format="full")`` resource to an :class:`InboundMessage`.

    Args:
        resource: The Gmail message resource (``id``, ``threadId``,
            ``labelIds``, ``internalDate``, ``payload``).

    Returns:
        An :class:`InboundMessage` with lower-cased headers, the sender split
        into name and address, ``To`` recipients, the plain-text body (HTML
        converted when no text part exists) and an aware UTC date.
    """
    payload = resource.get("payload") or {}
    headers = _header_map(payload.get("headers"))
    from_name, from_email = email.utils.parseaddr(headers.get("from", ""))
    to_addresses = [
        addr.strip().lower()
        for _name, addr in email.utils.getaddresses([headers.get("to", "")])
        if addr and addr.strip()
    ]
    return InboundMessage(
        message_id=str(resource.get("id", "")),
        thread_id=str(resource.get("threadId", "")),
        from_email=from_email,
        from_name=from_name.strip(),
        subject=headers.get("subject", ""),
        body_text=_extract_body(payload),
        date=_message_date(resource, headers),
        to=to_addresses,
        rfc_message_id=headers.get("message-id", ""),
        in_reply_to=headers.get("in-reply-to", ""),
        references=headers.get("references", ""),
        headers=headers,
        label_ids=list(resource.get("labelIds") or []),
    )


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


class DryRunMailer:
    """Mailer that records messages instead of sending them.

    ``sent`` and ``drafts`` hold every :class:`OutboundMessage` in order;
    ``inbox`` is what :meth:`fetch_inbound` / :meth:`fetch_thread` read from
    (tests and the CLI can pre-load it); ``labels`` maps thread ids to the
    label names applied. When ``outbox_dir`` is given, every message is also
    written there as ``<n>-sent.eml`` / ``<n>-draft.eml``.
    """

    def __init__(
        self,
        outbox_dir: str | os.PathLike[str] | None = None,
        *,
        sender_email: str = "",
        sender_name: str = "",
    ) -> None:
        self.outbox_dir: Path | None = Path(outbox_dir) if outbox_dir else None
        self.sender_email = sender_email.strip().lower()
        self.sender_name = sender_name
        self.sent: list[OutboundMessage] = []
        self.drafts: list[OutboundMessage] = []
        self.inbox: list[InboundMessage] = []
        self.labels: dict[str, list[str]] = {}
        self._counter = 0

    def _record(self, message: OutboundMessage, bucket: list[OutboundMessage], kind: str) -> SendResult:
        self._counter += 1
        number = self._counter
        bucket.append(message)
        if self.outbox_dir is not None:
            self.outbox_dir.mkdir(parents=True, exist_ok=True)
            mime = build_mime(message, sender_email=self.sender_email, sender_name=self.sender_name)
            (self.outbox_dir / f"{number}-{kind}.eml").write_bytes(mime.as_bytes())
        return SendResult(
            message_id=f"dry-{number}",
            thread_id=message.thread_id or f"dry-thread-{number}",
            draft_id=f"dry-draft-{number}" if kind == "draft" else "",
            dry_run=True,
        )

    def send(self, message: OutboundMessage) -> SendResult:
        """Record ``message`` as sent (and write ``<n>-sent.eml`` if configured)."""
        return self._record(message, self.sent, "sent")

    def create_draft(self, message: OutboundMessage) -> SendResult:
        """Record ``message`` as a draft (and write ``<n>-draft.eml`` if configured)."""
        return self._record(message, self.drafts, "draft")

    def fetch_inbound(self, since: datetime, *, max_results: int = 200) -> list[InboundMessage]:
        """Messages in ``inbox`` dated strictly after ``since``, oldest first."""
        since = _ensure_aware(since)
        messages = sorted((m for m in self.inbox if m.date > since), key=lambda m: m.date)
        return messages[: max(0, max_results)]

    def fetch_thread(self, thread_id: str) -> list[InboundMessage]:
        """Messages in ``inbox`` belonging to ``thread_id``, oldest first."""
        return sorted((m for m in self.inbox if m.thread_id == thread_id), key=lambda m: m.date)

    def add_label(self, thread_id: str, label: str) -> None:
        """Record that ``label`` was applied to ``thread_id``."""
        applied = self.labels.setdefault(thread_id, [])
        if label not in applied:
            applied.append(label)

    def thread_url(self, thread_id: str) -> str:
        """Dry-run threads have no web URL."""
        return ""


# --------------------------------------------------------------------------- #
# Gmail
# --------------------------------------------------------------------------- #


class GmailMailer:
    """Gmail REST API transport authenticated with an OAuth refresh token.

    Args:
        client_id: OAuth client id of the installed/desktop app.
        client_secret: OAuth client secret.
        refresh_token: Long-lived refresh token for the mailbox owner.
        sender_email: Address of the mailbox (``From`` and ``-from:`` filter).
        sender_name: Display name for ``From``.
        session: ``requests.Session`` to use (injectable for tests).
        timeout: Per-request timeout in seconds.
    """

    API = "https://gmail.googleapis.com/gmail/v1/users/me"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    REFRESH_MARGIN_SECONDS = 60.0

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        sender_email: str,
        sender_name: str = "",
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self.sender_email = sender_email.strip().lower()
        self.sender_name = sender_name
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._label_ids: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    def access_token(self) -> str:
        """Return a valid access token, refreshing when missing or about to expire."""
        if (
            self._access_token is None
            or time.time() >= self._token_expires_at - self.REFRESH_MARGIN_SECONDS
        ):
            self._refresh_access_token()
        assert self._access_token is not None
        return self._access_token

    def _refresh_access_token(self) -> None:
        response = self._session.post(
            self.TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=self._timeout,
        )
        if not 200 <= response.status_code < 300:
            raise MailerError(f"Gmail token {response.status_code}: {response.text}")
        try:
            data = response.json()
        except ValueError as exc:
            raise MailerError("Gmail token: response was not JSON") from exc
        token = data.get("access_token")
        if not token:
            raise MailerError("Gmail token: response has no access_token")
        self._access_token = str(token)
        self._token_expires_at = time.time() + float(data.get("expires_in", 3600))

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict:
        """Authenticated request to ``{API}/{path}`` with one retry on 401.

        Raises:
            MailerError: On any non-2xx response (other than a first 401).
        """
        url = f"{self.API}/{path}"
        func = self._session.get if method == "GET" else self._session.post
        for attempt in range(2):
            kwargs: dict[str, Any] = {
                "headers": {"Authorization": f"Bearer {self.access_token()}"},
                "timeout": self._timeout,
            }
            if params is not None:
                kwargs["params"] = params
            if json is not None:
                kwargs["json"] = json
            response = func(url, **kwargs)
            if response.status_code == 401 and attempt == 0:
                self._access_token = None  # force a refresh and try once more
                continue
            if not 200 <= response.status_code < 300:
                raise MailerError(f"Gmail {response.status_code}: {response.text}")
            if response.status_code == 204:
                return {}
            try:
                return response.json() or {}
            except ValueError:
                return {}
        raise MailerError("Gmail 401: unauthorized after token refresh")  # pragma: no cover

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict:
        return self._call("GET", path, params=params)

    def _post(self, path: str, *, json: dict[str, Any] | None = None) -> dict:
        return self._call("POST", path, json=json)

    def _raw(self, message: OutboundMessage) -> dict[str, Any]:
        mime = build_mime(message, sender_email=self.sender_email, sender_name=self.sender_name)
        body: dict[str, Any] = {"raw": _encode_raw(mime)}
        if message.thread_id:
            body["threadId"] = message.thread_id
        return body

    # ------------------------------------------------------------------ #
    # Mailer protocol
    # ------------------------------------------------------------------ #
    def send(self, message: OutboundMessage) -> SendResult:
        """``POST messages/send`` with the base64url MIME (``threadId`` for replies)."""
        resp = self._post("messages/send", json=self._raw(message))
        return SendResult(message_id=str(resp["id"]), thread_id=str(resp.get("threadId", "")))

    def create_draft(self, message: OutboundMessage) -> SendResult:
        """``POST drafts`` wrapping the MIME in ``{"message": {...}}``."""
        resp = self._post("drafts", json={"message": self._raw(message)})
        inner = resp.get("message") or {}
        return SendResult(
            message_id=str(inner.get("id", "")),
            thread_id=str(inner.get("threadId", "")),
            draft_id=str(resp["id"]),
        )

    def fetch_inbound(self, since: datetime, *, max_results: int = 200) -> list[InboundMessage]:
        """List messages after ``since`` (excluding our own, drafts and chats) and fetch each in full.

        Pagination follows ``nextPageToken`` until ``max_results`` ids are
        collected. The result is sorted by date, oldest first.
        """
        if max_results <= 0:
            return []
        since = _ensure_aware(since)
        clauses = [f"after:{int(since.timestamp())}"]
        if self.sender_email:
            clauses.append(f"-from:{self.sender_email}")
        clauses.extend(["-in:drafts", "-in:chats"])
        query = " ".join(clauses)

        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_results:
            params: dict[str, Any] = {"q": query, "maxResults": min(max_results - len(ids), 500)}
            if page_token:
                params["pageToken"] = page_token
            page = self._get("messages", params=params)
            ids.extend(str(item["id"]) for item in page.get("messages") or [])
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        ids = ids[:max_results]

        messages = [
            parse_gmail_message(self._get(f"messages/{message_id}", params={"format": "full"}))
            for message_id in ids
        ]
        messages.sort(key=lambda m: m.date)
        return messages

    def fetch_thread(self, thread_id: str) -> list[InboundMessage]:
        """``GET threads/{id}?format=full`` → messages, oldest first."""
        data = self._get(f"threads/{thread_id}", params={"format": "full"})
        messages = [parse_gmail_message(item) for item in data.get("messages") or []]
        messages.sort(key=lambda m: m.date)
        return messages

    def _label_id(self, name: str) -> str:
        """Find a label id by name (case-insensitive), creating the label if missing."""
        key = name.strip().lower()
        if key in self._label_ids:
            return self._label_ids[key]
        listing = self._get("labels")
        for label in listing.get("labels") or []:
            if str(label.get("name", "")).strip().lower() == key:
                self._label_ids[key] = str(label["id"])
                return self._label_ids[key]
        created = self._post(
            "labels",
            json={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        )
        self._label_ids[key] = str(created["id"])
        return self._label_ids[key]

    def add_label(self, thread_id: str, label: str) -> None:
        """Apply ``label`` to the thread, creating the label when it does not exist."""
        label_id = self._label_id(label)
        self._post(f"threads/{thread_id}/modify", json={"addLabelIds": [label_id]})

    def thread_url(self, thread_id: str) -> str:
        """Web link to the thread in the Gmail UI."""
        return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #


def build_mailer(settings: Settings) -> Mailer:
    """Pick the transport for ``settings``.

    ``policy.live`` false (the default) → :class:`DryRunMailer` writing to
    ``settings.outbox_dir``. Live → :class:`GmailMailer`, after
    ``settings.validate_for_live()`` has confirmed the OAuth credentials.

    Raises:
        ValueError: If live mode is requested without Gmail credentials.
    """
    if settings.policy.live:
        settings.validate_for_live()
        return GmailMailer(
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            refresh_token=settings.gmail_refresh_token,
            sender_email=settings.sender_email,
            sender_name=settings.sender_name,
        )
    return DryRunMailer(
        settings.outbox_dir or None,
        sender_email=settings.sender_email,
        sender_name=settings.sender_name,
    )
