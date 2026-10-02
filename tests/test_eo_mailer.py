"""Tests for the email_offerings.mailer module (no network access)."""

from __future__ import annotations

import base64
import email
import email.policy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from email_offerings.config import Policy, Settings
from email_offerings.mailer import (
    DryRunMailer,
    GmailMailer,
    Mailer,
    MailerError,
    build_mailer,
    build_mime,
    html_to_text,
    parse_gmail_message,
)
from email_offerings.models import InboundMessage, OutboundMessage, SendResult

SENDER = "dan@example.com"
SENDER_NAME = "Dan Example"
UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _outbound(**overrides) -> OutboundMessage:
    data = dict(
        subject="$0.99/sf Silver Rustic Oak SPC (New 10/1)",
        body_text="Truckload available FOB Calhoun, GA.\n-Dan",
        to=[SENDER],
        bcc=["buyer1@example.com", "buyer2@example.com"],
    )
    data.update(overrides)
    return OutboundMessage(**data)


def _b64url(text: str, *, encoding: str = "utf-8") -> str:
    """Encode the way Gmail does: base64url without padding."""
    return base64.urlsafe_b64encode(text.encode(encoding)).decode("ascii").rstrip("=")


def _part(mime_type: str, text: str | None = None, *, headers=None, parts=None, filename="", encoding="utf-8"):
    part = {"mimeType": mime_type, "filename": filename, "headers": headers or [], "body": {}}
    if text is not None:
        part["body"] = {"size": len(text), "data": _b64url(text, encoding=encoding)}
    if parts is not None:
        part["parts"] = parts
    return part


def _resource(
    *,
    message_id="msg-1",
    thread_id="thread-1",
    internal_date="1759363200000",  # 2025-10-02T00:00:00Z
    headers=None,
    payload=None,
    label_ids=("INBOX", "UNREAD"),
):
    default_headers = [
        {"name": "From", "value": "Jordan Buyer <jordan@example.com>"},
        {"name": "To", "value": f"{SENDER_NAME} <{SENDER}>"},
        {"name": "Subject", "value": "Re: $0.99/sf Silver Rustic Oak SPC (New 10/1)"},
        {"name": "Message-ID", "value": "<abc123@mail.example.com>"},
        {"name": "In-Reply-To", "value": "<orig@example.com>"},
        {"name": "References", "value": "<orig@example.com> <other@example.com>"},
        {"name": "Date", "value": "Thu, 02 Oct 2025 00:00:00 +0000"},
    ]
    if payload is None:
        payload = _part("text/plain", "How cheap can you get on 2 truckloads delivered to 73127?")
    payload = dict(payload)
    # Gmail puts the RFC 822 headers and the top-level Content-Type on the same payload.
    payload["headers"] = [*(headers if headers is not None else default_headers), *payload.get("headers", [])]
    resource = {"id": message_id, "threadId": thread_id, "labelIds": list(label_ids), "payload": payload}
    if internal_date is not None:
        resource["internalDate"] = internal_date
    return resource


def _response(status_code=200, json_data=None, text="", json_error=False):
    """Build a MagicMock mimicking a ``requests.Response``."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_error:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


def _token_response(token="tok-1", expires_in=3600):
    return _response(200, {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"})


def _gmail(session=None, **overrides) -> tuple[GmailMailer, MagicMock]:
    session = session or MagicMock()
    kwargs = dict(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        sender_email=SENDER,
        sender_name=SENDER_NAME,
        session=session,
    )
    kwargs.update(overrides)
    return GmailMailer(**kwargs), session


def _decode_raw(raw: str) -> email.message.EmailMessage:
    data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    return email.message_from_bytes(data, policy=email.policy.default)


def _inbound(message_id, thread_id, date, from_email="buyer@example.com") -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        thread_id=thread_id,
        from_email=from_email,
        subject="Re: offer",
        body_text="interested",
        date=date,
    )


# --------------------------------------------------------------------------- #
# MailerError / protocol
# --------------------------------------------------------------------------- #


class TestMailerProtocol:
    def test_mailer_error_is_runtime_error(self):
        assert issubclass(MailerError, RuntimeError)

    def test_dry_run_mailer_satisfies_protocol(self):
        assert isinstance(DryRunMailer(), Mailer)

    def test_gmail_mailer_satisfies_protocol(self):
        mailer, _ = _gmail()
        assert isinstance(mailer, Mailer)


# --------------------------------------------------------------------------- #
# build_mime
# --------------------------------------------------------------------------- #


class TestBuildMime:
    def test_from_header_uses_display_name(self):
        mime = build_mime(_outbound(), sender_email=SENDER, sender_name=SENDER_NAME)
        assert mime["From"] == f"{SENDER_NAME} <{SENDER}>"

    def test_from_without_name_is_bare_address(self):
        mime = build_mime(_outbound(), sender_email=SENDER)
        assert mime["From"] == SENDER

    def test_from_header_omitted_when_sender_empty(self):
        mime = build_mime(_outbound(), sender_email="")
        assert mime["From"] is None
        assert mime["Message-ID"]  # still generated

    def test_recipient_headers_are_comma_joined(self):
        message = _outbound(to=["a@example.com", "b@example.com"], cc=["c@example.com"], bcc=["d@example.com", "e@example.com"])
        mime = build_mime(message, sender_email=SENDER)
        assert mime["To"] == "a@example.com, b@example.com"
        assert mime["Cc"] == "c@example.com"
        assert mime["Bcc"] == "d@example.com, e@example.com"

    def test_bcc_header_is_present_for_gmail_envelope(self):
        mime = build_mime(_outbound(), sender_email=SENDER)
        assert "Bcc" in mime
        assert "buyer1@example.com" in mime["Bcc"]
        assert "buyer2@example.com" in mime["Bcc"]

    def test_optional_headers_absent_when_not_given(self):
        mime = build_mime(_outbound(cc=[]), sender_email=SENDER)
        for name in ("Cc", "Reply-To", "In-Reply-To", "References"):
            assert mime[name] is None

    def test_subject_reply_to_and_threading_headers(self):
        message = _outbound(
            reply_to="sales@example.com",
            in_reply_to="<orig@example.com>",
            references="<orig@example.com> <two@example.com>",
        )
        mime = build_mime(message, sender_email=SENDER)
        assert mime["Subject"] == message.subject
        assert mime["Reply-To"] == "sales@example.com"
        assert mime["In-Reply-To"] == "<orig@example.com>"
        assert mime["References"] == "<orig@example.com> <two@example.com>"

    def test_custom_headers_added(self):
        message = _outbound(headers={"X-Offering-Id": "oak-2026-10-01", "X-Campaign": "blast-1"})
        mime = build_mime(message, sender_email=SENDER)
        assert mime["X-Offering-Id"] == "oak-2026-10-01"
        assert mime["X-Campaign"] == "blast-1"

    def test_custom_header_replaces_existing_header(self):
        message = _outbound(reply_to="first@example.com", headers={"Reply-To": "second@example.com"})
        mime = build_mime(message, sender_email=SENDER)
        assert mime.get_all("Reply-To") == ["second@example.com"]

    def test_date_header_is_recent_and_parseable(self):
        before = datetime.now(UTC) - timedelta(seconds=5)
        mime = build_mime(_outbound(), sender_email=SENDER)
        parsed = email.utils.parsedate_to_datetime(mime["Date"])
        assert parsed.tzinfo is not None
        assert before <= parsed <= datetime.now(UTC) + timedelta(seconds=5)

    def test_message_id_uses_sender_domain(self):
        mime = build_mime(_outbound(), sender_email=SENDER)
        assert mime["Message-ID"].startswith("<")
        assert mime["Message-ID"].endswith("@example.com>")

    def test_text_only_body(self):
        mime = build_mime(_outbound(body_text="Plain text only."), sender_email=SENDER)
        assert mime.get_content_type() == "text/plain"
        assert mime.get_content().strip() == "Plain text only."

    def test_html_alternative_added(self):
        message = _outbound(body_text="Plain", body_html="<p>Plain <b>rich</b></p>")
        mime = build_mime(message, sender_email=SENDER)
        assert mime.get_content_type() == "multipart/alternative"
        assert mime.get_body(preferencelist=("plain",)).get_content().strip() == "Plain"
        assert "<b>rich</b>" in mime.get_body(preferencelist=("html",)).get_content()

    def test_attachment_with_guessed_mime_type(self, tmp_path):
        sheet = tmp_path / "pricing.csv"
        sheet.write_text("sku,price\nOAK,0.99\n", encoding="utf-8")
        pdf = tmp_path / "spec.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        message = _outbound(attachments=[str(sheet), str(pdf)])
        mime = build_mime(message, sender_email=SENDER)
        assert mime.get_content_type() == "multipart/mixed"
        attachments = list(mime.iter_attachments())
        assert [a.get_filename() for a in attachments] == ["pricing.csv", "spec.pdf"]
        assert attachments[0].get_content_type() == "text/csv"
        assert attachments[1].get_content_type() == "application/pdf"
        assert attachments[1].get_payload(decode=True) == b"%PDF-1.4 fake"

    def test_attachment_unknown_extension_is_octet_stream(self, tmp_path):
        blob = tmp_path / "data.unknownext"
        blob.write_bytes(b"\x00\x01binary")
        mime = build_mime(_outbound(attachments=[blob]), sender_email=SENDER)
        (attachment,) = mime.iter_attachments()
        assert attachment.get_content_type() == "application/octet-stream"
        assert attachment.get_payload(decode=True) == b"\x00\x01binary"

    def test_compressed_attachment_type_falls_back_to_octet_stream(self, tmp_path):
        archive = tmp_path / "photos.tar.gz"
        archive.write_bytes(b"gz")
        mime = build_mime(_outbound(attachments=[str(archive)]), sender_email=SENDER)
        (attachment,) = mime.iter_attachments()
        assert attachment.get_content_type() == "application/octet-stream"

    def test_html_and_attachment_together(self, tmp_path):
        image = tmp_path / "oak.png"
        image.write_bytes(b"\x89PNG fake")
        message = _outbound(body_html="<p>hi</p>", attachments=[str(image)])
        mime = build_mime(message, sender_email=SENDER)
        assert mime.get_content_type() == "multipart/mixed"
        assert mime.get_body(preferencelist=("html",)) is not None
        (attachment,) = mime.iter_attachments()
        assert attachment.get_content_type() == "image/png"

    def test_missing_attachment_raises_mailer_error(self, tmp_path):
        missing = tmp_path / "nope.pdf"
        with pytest.raises(MailerError, match="Attachment not found"):
            build_mime(_outbound(attachments=[str(missing)]), sender_email=SENDER)


# --------------------------------------------------------------------------- #
# html_to_text
# --------------------------------------------------------------------------- #


class TestHtmlToText:
    def test_strips_tags(self):
        assert html_to_text("<span>Hello <b>world</b></span>") == "Hello world"

    def test_br_and_p_become_newlines(self):
        text = html_to_text("<p>First line<br>second line</p><p>Second paragraph</p>")
        assert text == "First line\nsecond line\n\nSecond paragraph"

    def test_self_closing_br_variants(self):
        assert html_to_text("a<br/>b<br />c<BR>d") == "a\nb\nc\nd"

    def test_unescapes_entities(self):
        assert html_to_text("Tom &amp; Jerry&nbsp;&lt;3 &#39;quoted&#39;") == "Tom & Jerry <3 'quoted'"

    def test_removes_script_and_style_blocks(self):
        html = "<style>p{color:red}</style><script>alert('x')</script><p>Visible</p>"
        assert html_to_text(html) == "Visible"

    def test_collapses_whitespace_and_blank_lines(self):
        html = "<div>  lots   of\t spaces </div>\n\n\n<div></div><div></div><div>end</div>"
        assert html_to_text(html) == "lots of spaces\n\nend"

    def test_list_items_and_headings_break_lines(self):
        html = "<h1>Title</h1><ul><li>one</li><li>two</li></ul>"
        assert html_to_text(html) == "Title\n\none\n\ntwo"

    def test_empty_input(self):
        assert html_to_text("") == ""


# --------------------------------------------------------------------------- #
# parse_gmail_message
# --------------------------------------------------------------------------- #


class TestParseGmailMessage:
    def test_basic_fields(self):
        inbound = parse_gmail_message(_resource())
        assert inbound.message_id == "msg-1"
        assert inbound.thread_id == "thread-1"
        assert inbound.from_email == "jordan@example.com"
        assert inbound.from_name == "Jordan Buyer"
        assert inbound.subject == "Re: $0.99/sf Silver Rustic Oak SPC (New 10/1)"
        assert inbound.body_text == "How cheap can you get on 2 truckloads delivered to 73127?"
        assert inbound.label_ids == ["INBOX", "UNREAD"]

    def test_headers_lower_cased_and_threading_headers_copied(self):
        inbound = parse_gmail_message(_resource())
        assert "from" in inbound.headers
        assert "subject" in inbound.headers
        assert "Subject" not in inbound.headers
        assert inbound.rfc_message_id == "<abc123@mail.example.com>"
        assert inbound.in_reply_to == "<orig@example.com>"
        assert inbound.references == "<orig@example.com> <other@example.com>"

    def test_duplicate_headers_keep_first_value(self):
        headers = [
            {"name": "Received", "value": "first hop"},
            {"name": "Received", "value": "second hop"},
            {"name": "From", "value": "x@example.com"},
        ]
        inbound = parse_gmail_message(_resource(headers=headers))
        assert inbound.headers["received"] == "first hop"

    def test_to_list_parsed_with_getaddresses(self):
        headers = [
            {"name": "From", "value": "buyer@example.com"},
            {"name": "To", "value": f'"Dan Example" <{SENDER}>, Sales Team <sales@example.com>,ops@example.com'},
        ]
        inbound = parse_gmail_message(_resource(headers=headers))
        assert inbound.to == [SENDER, "sales@example.com", "ops@example.com"]

    def test_from_without_display_name(self):
        headers = [{"name": "From", "value": "plain@example.com"}]
        inbound = parse_gmail_message(_resource(headers=headers))
        assert inbound.from_email == "plain@example.com"
        assert inbound.from_name == ""

    def test_from_email_is_lower_cased(self):
        headers = [{"name": "From", "value": "Mixed Case <Buyer.One@Example.COM>"}]
        inbound = parse_gmail_message(_resource(headers=headers))
        assert inbound.from_email == "buyer.one@example.com"

    def test_internal_date_ms_to_aware_utc(self):
        inbound = parse_gmail_message(_resource(internal_date="1759363200000"))
        assert inbound.date == datetime(2025, 10, 2, 0, 0, tzinfo=UTC)
        assert inbound.date.tzinfo is not None

    def test_internal_date_accepts_int(self):
        inbound = parse_gmail_message(_resource(internal_date=1759363200500))
        assert inbound.date == datetime(2025, 10, 2, 0, 0, 0, 500000, tzinfo=UTC)

    def test_missing_internal_date_falls_back_to_date_header(self):
        headers = [{"name": "Date", "value": "Wed, 01 Oct 2025 08:30:00 -0400"}]
        inbound = parse_gmail_message(_resource(internal_date=None, headers=headers))
        assert inbound.date == datetime(2025, 10, 1, 12, 30, tzinfo=UTC)

    def test_naive_date_header_treated_as_utc(self):
        headers = [{"name": "Date", "value": "Wed, 01 Oct 2025 08:30:00 -0000"}]
        inbound = parse_gmail_message(_resource(internal_date=None, headers=headers))
        assert inbound.date == datetime(2025, 10, 1, 8, 30, tzinfo=UTC)

    def test_unparseable_date_header_falls_back_to_now(self):
        headers = [{"name": "Date", "value": "not a date"}]
        before = datetime.now(UTC) - timedelta(seconds=5)
        inbound = parse_gmail_message(_resource(internal_date=None, headers=headers))
        assert before <= inbound.date <= datetime.now(UTC) + timedelta(seconds=5)

    def test_no_date_at_all_falls_back_to_now(self):
        before = datetime.now(UTC) - timedelta(seconds=5)
        inbound = parse_gmail_message(_resource(internal_date="", headers=[]))
        assert before <= inbound.date <= datetime.now(UTC) + timedelta(seconds=5)

    def test_nested_multipart_prefers_text_plain(self):
        alternative = _part(
            "multipart/alternative",
            parts=[
                _part("text/plain", "Plain body here"),
                _part("text/html", "<p>HTML body here</p>"),
            ],
        )
        attachment = {
            "mimeType": "application/pdf",
            "filename": "spec.pdf",
            "headers": [],
            "body": {"attachmentId": "att-1", "size": 1234},
        }
        mixed = _part("multipart/mixed", parts=[alternative, attachment])
        inbound = parse_gmail_message(_resource(payload=mixed))
        assert inbound.body_text == "Plain body here"

    def test_html_only_message_converted_to_text(self):
        html = "<html><body><p>Hi Dan,</p><p>Interested in <b>2 trucks</b> &amp; more.<br>Thanks</p></body></html>"
        payload = _part("multipart/alternative", parts=[_part("text/html", html)])
        inbound = parse_gmail_message(_resource(payload=payload))
        assert inbound.body_text == "Hi Dan,\n\nInterested in 2 trucks & more.\nThanks"

    def test_text_plain_attachment_is_not_used_as_body(self):
        txt_attachment = dict(_part("text/plain", "attachment contents"), filename="notes.txt")
        payload = _part("multipart/mixed", parts=[txt_attachment, _part("text/html", "<p>Real body</p>")])
        inbound = parse_gmail_message(_resource(payload=payload))
        assert inbound.body_text == "Real body"

    def test_base64url_characters_and_missing_padding(self):
        text = "Price ~~~~ ok ??? >>> yes"  # produces '-' and '_' in base64url
        encoded = _b64url(text)
        assert "-" in encoded or "_" in encoded
        assert not encoded.endswith("=")
        payload = _part("text/plain", text)
        assert payload["body"]["data"] == encoded
        inbound = parse_gmail_message(_resource(payload=payload))
        assert inbound.body_text == text

    def test_crlf_normalised_in_plain_body(self):
        payload = _part("text/plain", "line one\r\nline two\r\n")
        inbound = parse_gmail_message(_resource(payload=payload))
        assert inbound.body_text == "line one\nline two\n"

    def test_charset_from_content_type_header(self):
        headers = [{"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}]
        payload = _part("text/plain", "Café flooring", headers=headers, encoding="iso-8859-1")
        inbound = parse_gmail_message(_resource(payload=payload))
        assert inbound.body_text == "Café flooring"

    def test_unknown_charset_falls_back_to_utf8(self):
        headers = [{"name": "Content-Type", "value": "text/plain; charset=x-not-a-charset"}]
        payload = _part("text/plain", "Café", headers=headers)
        inbound = parse_gmail_message(_resource(payload=payload))
        assert inbound.body_text == "Café"

    def test_empty_body_when_no_text_parts(self):
        payload = _part("multipart/mixed", parts=[{"mimeType": "application/pdf", "filename": "a.pdf", "body": {"attachmentId": "x"}}])
        inbound = parse_gmail_message(_resource(payload=payload))
        assert inbound.body_text == ""

    def test_missing_payload_and_labels(self):
        inbound = parse_gmail_message({"id": "bare", "threadId": "t", "internalDate": "0"})
        assert inbound.message_id == "bare"
        assert inbound.body_text == ""
        assert inbound.label_ids == []
        assert inbound.headers == {}
        assert inbound.date == datetime(1970, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# DryRunMailer
# --------------------------------------------------------------------------- #


class TestDryRunMailer:
    def test_send_records_and_returns_fake_ids(self):
        mailer = DryRunMailer()
        result = mailer.send(_outbound())
        assert result == SendResult(message_id="dry-1", thread_id="dry-thread-1", draft_id="", dry_run=True)
        assert mailer.sent == [_outbound()]
        assert mailer.drafts == []

    def test_send_keeps_existing_thread_id(self):
        mailer = DryRunMailer()
        result = mailer.send(_outbound(thread_id="thread-xyz"))
        assert result.thread_id == "thread-xyz"
        assert result.dry_run is True

    def test_create_draft_records_with_draft_id(self):
        mailer = DryRunMailer()
        result = mailer.create_draft(_outbound())
        assert result.message_id == "dry-1"
        assert result.draft_id == "dry-draft-1"
        assert result.dry_run is True
        assert mailer.drafts == [_outbound()]
        assert mailer.sent == []

    def test_counter_shared_between_sent_and_drafts(self):
        mailer = DryRunMailer()
        first = mailer.send(_outbound())
        second = mailer.create_draft(_outbound())
        third = mailer.send(_outbound())
        assert [first.message_id, second.message_id, third.message_id] == ["dry-1", "dry-2", "dry-3"]

    def test_writes_numbered_eml_files_and_creates_directory(self, tmp_path):
        outbox = tmp_path / "nested" / "outbox"
        mailer = DryRunMailer(outbox, sender_email=SENDER, sender_name=SENDER_NAME)
        mailer.send(_outbound(subject="First blast"))
        mailer.create_draft(_outbound(subject="Second draft"))
        assert outbox.is_dir()
        names = sorted(p.name for p in outbox.iterdir())
        assert names == ["1-sent.eml", "2-draft.eml"]
        first = email.message_from_bytes((outbox / "1-sent.eml").read_bytes(), policy=email.policy.default)
        assert first["Subject"] == "First blast"
        assert first["From"] == f"{SENDER_NAME} <{SENDER}>"
        assert "buyer1@example.com" in first["Bcc"]
        second = email.message_from_bytes((outbox / "2-draft.eml").read_bytes(), policy=email.policy.default)
        assert second["Subject"] == "Second draft"

    def test_outbox_dir_accepts_string(self, tmp_path):
        mailer = DryRunMailer(str(tmp_path / "out"))
        mailer.send(_outbound())
        assert (tmp_path / "out" / "1-sent.eml").exists()

    def test_no_files_without_outbox_dir(self, tmp_path):
        mailer = DryRunMailer()
        mailer.send(_outbound())
        assert mailer.outbox_dir is None
        assert list(tmp_path.iterdir()) == []

    def test_empty_outbox_dir_means_none(self):
        assert DryRunMailer("").outbox_dir is None

    def test_eml_written_without_sender(self, tmp_path):
        mailer = DryRunMailer(tmp_path)
        mailer.send(_outbound())
        parsed = email.message_from_bytes((tmp_path / "1-sent.eml").read_bytes(), policy=email.policy.default)
        assert parsed["From"] is None
        assert parsed["To"] == SENDER

    def test_fetch_inbound_filters_strictly_after_since_sorted(self):
        mailer = DryRunMailer()
        base = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        older = _inbound("old", "t1", base - timedelta(hours=1))
        exact = _inbound("exact", "t1", base)
        newer = _inbound("new", "t2", base + timedelta(hours=2))
        newest = _inbound("newest", "t3", base + timedelta(hours=3))
        mailer.inbox.extend([newest, older, newer, exact])
        assert [m.message_id for m in mailer.fetch_inbound(base)] == ["new", "newest"]

    def test_fetch_inbound_respects_max_results(self):
        mailer = DryRunMailer()
        base = datetime(2026, 10, 1, tzinfo=UTC)
        for i in range(5):
            mailer.inbox.append(_inbound(f"m{i}", "t", base + timedelta(minutes=i + 1)))
        assert [m.message_id for m in mailer.fetch_inbound(base, max_results=2)] == ["m0", "m1"]
        assert mailer.fetch_inbound(base, max_results=0) == []

    def test_fetch_inbound_naive_since_treated_as_utc(self):
        mailer = DryRunMailer()
        mailer.inbox.append(_inbound("m", "t", datetime(2026, 10, 1, 13, tzinfo=UTC)))
        assert len(mailer.fetch_inbound(datetime(2026, 10, 1, 12))) == 1
        assert mailer.fetch_inbound(datetime(2026, 10, 1, 14)) == []

    def test_fetch_thread_filters_by_thread_id(self):
        mailer = DryRunMailer()
        base = datetime(2026, 10, 1, tzinfo=UTC)
        mailer.inbox.extend(
            [
                _inbound("b", "t1", base + timedelta(hours=1)),
                _inbound("x", "t2", base),
                _inbound("a", "t1", base),
            ]
        )
        assert [m.message_id for m in mailer.fetch_thread("t1")] == ["a", "b"]
        assert mailer.fetch_thread("missing") == []

    def test_add_label_records_without_duplicates(self):
        mailer = DryRunMailer()
        mailer.add_label("t1", "Offerings/Offer")
        mailer.add_label("t1", "Offerings/Offer")
        mailer.add_label("t1", "Offerings/Needs reply")
        mailer.add_label("t2", "Offerings/Offer")
        assert mailer.labels == {"t1": ["Offerings/Offer", "Offerings/Needs reply"], "t2": ["Offerings/Offer"]}

    def test_thread_url_is_empty(self):
        assert DryRunMailer().thread_url("t1") == ""


# --------------------------------------------------------------------------- #
# GmailMailer
# --------------------------------------------------------------------------- #


class TestGmailMailerAuth:
    def test_access_token_posts_refresh_request_and_caches(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response("tok-1", 3600)
        with patch("email_offerings.mailer.time") as mock_time:
            mock_time.time.return_value = 1_000.0
            assert mailer.access_token() == "tok-1"
            assert mailer.access_token() == "tok-1"
        session.post.assert_called_once_with(
            GmailMailer.TOKEN_URL,
            data={
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )

    def test_access_token_refreshes_sixty_seconds_before_expiry(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response("tok-1", 3600), _token_response("tok-2", 3600)]
        with patch("email_offerings.mailer.time") as mock_time:
            mock_time.time.return_value = 1_000.0
            assert mailer.access_token() == "tok-1"
            mock_time.time.return_value = 1_000.0 + 3600 - 61  # still valid
            assert mailer.access_token() == "tok-1"
            assert session.post.call_count == 1
            mock_time.time.return_value = 1_000.0 + 3600 - 60  # inside the margin
            assert mailer.access_token() == "tok-2"
            assert session.post.call_count == 2

    def test_token_endpoint_error_raises_with_status_and_body(self):
        mailer, session = _gmail()
        session.post.return_value = _response(400, {"error": "invalid_grant"}, text='{"error":"invalid_grant"}')
        with pytest.raises(MailerError, match=r"Gmail token 400: .*invalid_grant"):
            mailer.access_token()

    def test_token_endpoint_non_json_raises(self):
        mailer, session = _gmail()
        session.post.return_value = _response(200, json_error=True, text="<html>")
        with pytest.raises(MailerError, match="not JSON"):
            mailer.access_token()

    def test_token_endpoint_missing_access_token_raises(self):
        mailer, session = _gmail()
        session.post.return_value = _response(200, {"expires_in": 3600})
        with pytest.raises(MailerError, match="no access_token"):
            mailer.access_token()

    def test_default_expiry_when_expires_in_missing(self):
        mailer, session = _gmail()
        session.post.return_value = _response(200, {"access_token": "tok"})
        with patch("email_offerings.mailer.time") as mock_time:
            mock_time.time.return_value = 0.0
            mailer.access_token()
            mock_time.time.return_value = 3600 - 61
            mailer.access_token()
        assert session.post.call_count == 1

    def test_creates_own_session_when_none_given(self):
        import requests

        mailer = GmailMailer(client_id="a", client_secret="b", refresh_token="c", sender_email=SENDER)
        assert isinstance(mailer._session, requests.Session)

    def test_custom_timeout_used_for_requests(self):
        mailer, session = _gmail(timeout=5.0)
        session.post.side_effect = [_token_response(), _response(200, {"id": "m", "threadId": "t"})]
        mailer.send(_outbound())
        assert session.post.call_args_list[0].kwargs["timeout"] == 5.0
        assert session.post.call_args_list[1].kwargs["timeout"] == 5.0

    def test_sender_email_normalised(self):
        mailer, _ = _gmail(sender_email="  Dan@Example.COM ")
        assert mailer.sender_email == "dan@example.com"


class TestGmailMailerSend:
    def test_send_posts_base64url_mime_with_bearer_header(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response("tok-1"), _response(200, {"id": "msg-9", "threadId": "thr-9"})]
        message = _outbound(thread_id="thr-9", body_html="<p>hi</p>")

        result = mailer.send(message)

        assert result == SendResult(message_id="msg-9", thread_id="thr-9", draft_id="", dry_run=False)
        call = session.post.call_args_list[1]
        assert call.args[0] == f"{GmailMailer.API}/messages/send"
        assert call.kwargs["headers"] == {"Authorization": "Bearer tok-1"}
        payload = call.kwargs["json"]
        assert payload["threadId"] == "thr-9"
        assert set(payload) == {"raw", "threadId"}
        assert set(payload["raw"]) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
        mime = _decode_raw(payload["raw"])
        assert mime["Subject"] == message.subject
        assert mime["Bcc"] == "buyer1@example.com, buyer2@example.com"
        assert mime["From"] == f"{SENDER_NAME} <{SENDER}>"
        assert mime["To"] == SENDER
        assert mime.get_content_type() == "multipart/alternative"

    def test_send_without_thread_id_omits_thread_id(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(200, {"id": "msg-1", "threadId": "thr-1"})]
        mailer.send(_outbound())
        payload = session.post.call_args_list[1].kwargs["json"]
        assert "threadId" not in payload
        assert "raw" in payload

    def test_send_missing_thread_id_in_response(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(200, {"id": "msg-1"})]
        assert mailer.send(_outbound()).thread_id == ""

    def test_create_draft_payload_and_result(self):
        mailer, session = _gmail()
        session.post.side_effect = [
            _token_response(),
            _response(200, {"id": "draft-5", "message": {"id": "msg-5", "threadId": "thr-5"}}),
        ]
        message = _outbound(subject="Re: quote", thread_id="thr-5", in_reply_to="<orig@example.com>")

        result = mailer.create_draft(message)

        assert result == SendResult(message_id="msg-5", thread_id="thr-5", draft_id="draft-5", dry_run=False)
        call = session.post.call_args_list[1]
        assert call.args[0] == f"{GmailMailer.API}/drafts"
        payload = call.kwargs["json"]
        assert set(payload) == {"message"}
        assert payload["message"]["threadId"] == "thr-5"
        mime = _decode_raw(payload["message"]["raw"])
        assert mime["Subject"] == "Re: quote"
        assert mime["In-Reply-To"] == "<orig@example.com>"

    def test_create_draft_without_message_block(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(200, {"id": "draft-1"})]
        result = mailer.create_draft(_outbound())
        assert result == SendResult(message_id="", thread_id="", draft_id="draft-1")

    def test_401_retried_once_after_forced_refresh(self):
        mailer, session = _gmail()
        session.post.side_effect = [
            _token_response("tok-old"),
            _response(401, {"error": "unauthorized"}, text="unauthorized"),
            _token_response("tok-new"),
            _response(200, {"id": "msg-2", "threadId": "thr-2"}),
        ]

        result = mailer.send(_outbound())

        assert result.message_id == "msg-2"
        assert session.post.call_count == 4
        assert session.post.call_args_list[0].args[0] == GmailMailer.TOKEN_URL
        assert session.post.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer tok-old"
        assert session.post.call_args_list[2].args[0] == GmailMailer.TOKEN_URL
        assert session.post.call_args_list[3].kwargs["headers"]["Authorization"] == "Bearer tok-new"

    def test_second_401_raises_mailer_error(self):
        mailer, session = _gmail()
        session.post.side_effect = [
            _token_response("tok-old"),
            _response(401, text="unauthorized"),
            _token_response("tok-new"),
            _response(401, text="still unauthorized"),
        ]
        with pytest.raises(MailerError, match="Gmail 401: still unauthorized"):
            mailer.send(_outbound())
        assert session.post.call_count == 4

    def test_other_error_raises_with_status_and_body(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(403, text="Rate limit exceeded")]
        with pytest.raises(MailerError, match="Gmail 403: Rate limit exceeded"):
            mailer.send(_outbound())
        assert session.post.call_count == 2  # no retry for non-401

    def test_missing_attachment_raises_before_any_http(self, tmp_path):
        mailer, session = _gmail()
        with pytest.raises(MailerError, match="Attachment not found"):
            mailer.send(_outbound(attachments=[str(tmp_path / "missing.pdf")]))
        session.post.assert_not_called()


class TestGmailMailerFetch:
    def test_fetch_inbound_query_pagination_and_sorting(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response("tok")
        since = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        newer = _resource(message_id="m-new", thread_id="t2", internal_date="1759400000000")
        older = _resource(message_id="m-old", thread_id="t1", internal_date="1759300000000")
        mid = _resource(message_id="m-mid", thread_id="t1", internal_date="1759350000000")
        session.get.side_effect = [
            _response(200, {"messages": [{"id": "m-new"}, {"id": "m-old"}], "nextPageToken": "page-2"}),
            _response(200, {"messages": [{"id": "m-mid"}]}),
            _response(200, newer),
            _response(200, older),
            _response(200, mid),
        ]

        messages = mailer.fetch_inbound(since, max_results=200)

        assert [m.message_id for m in messages] == ["m-old", "m-mid", "m-new"]
        assert all(m.date.tzinfo is not None for m in messages)
        calls = session.get.call_args_list
        expected_q = f"after:{int(since.timestamp())} -from:{SENDER} -in:drafts -in:chats"
        assert calls[0].args[0] == f"{GmailMailer.API}/messages"
        assert calls[0].kwargs["params"] == {"q": expected_q, "maxResults": 200}
        assert calls[0].kwargs["headers"] == {"Authorization": "Bearer tok"}
        assert calls[1].kwargs["params"] == {"q": expected_q, "maxResults": 198, "pageToken": "page-2"}
        for call, message_id in zip(calls[2:], ["m-new", "m-old", "m-mid"]):
            assert call.args[0] == f"{GmailMailer.API}/messages/{message_id}"
            assert call.kwargs["params"] == {"format": "full"}
        assert session.post.call_count == 1  # token fetched once for all five calls

    def test_fetch_inbound_stops_paging_at_max_results(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.side_effect = [
            _response(200, {"messages": [{"id": "a"}, {"id": "b"}, {"id": "c"}], "nextPageToken": "more"}),
            _response(200, _resource(message_id="a")),
            _response(200, _resource(message_id="b")),
        ]
        messages = mailer.fetch_inbound(datetime(2026, 10, 1, tzinfo=UTC), max_results=2)
        assert [m.message_id for m in messages] == ["a", "b"]
        assert session.get.call_count == 3
        assert session.get.call_args_list[0].kwargs["params"]["maxResults"] == 2

    def test_fetch_inbound_caps_page_size_at_500(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.return_value = _response(200, {})
        assert mailer.fetch_inbound(datetime(2026, 10, 1, tzinfo=UTC), max_results=2000) == []
        assert session.get.call_args.kwargs["params"]["maxResults"] == 500

    def test_fetch_inbound_empty_mailbox(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.return_value = _response(200, {"resultSizeEstimate": 0})
        assert mailer.fetch_inbound(datetime(2026, 10, 1, tzinfo=UTC)) == []
        assert session.get.call_count == 1

    def test_fetch_inbound_zero_max_results_makes_no_calls(self):
        mailer, session = _gmail()
        assert mailer.fetch_inbound(datetime(2026, 10, 1, tzinfo=UTC), max_results=0) == []
        session.get.assert_not_called()
        session.post.assert_not_called()

    def test_fetch_inbound_naive_since_treated_as_utc(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.return_value = _response(200, {})
        mailer.fetch_inbound(datetime(2026, 10, 1, 12, 0))
        epoch = int(datetime(2026, 10, 1, 12, 0, tzinfo=UTC).timestamp())
        assert session.get.call_args.kwargs["params"]["q"].startswith(f"after:{epoch} ")

    def test_fetch_inbound_without_sender_omits_from_clause(self):
        mailer, session = _gmail(sender_email="")
        session.post.return_value = _token_response()
        session.get.return_value = _response(200, {})
        mailer.fetch_inbound(datetime(2026, 10, 1, tzinfo=UTC))
        q = session.get.call_args.kwargs["params"]["q"]
        assert "-from:" not in q
        assert q.endswith(" -in:drafts -in:chats")

    def test_fetch_inbound_401_on_list_is_retried(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response("tok-1"), _token_response("tok-2")]
        session.get.side_effect = [_response(401, text="expired"), _response(200, {})]
        assert mailer.fetch_inbound(datetime(2026, 10, 1, tzinfo=UTC)) == []
        assert session.get.call_args_list[1].kwargs["headers"] == {"Authorization": "Bearer tok-2"}

    def test_fetch_thread_returns_sorted_messages(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.return_value = _response(
            200,
            {
                "id": "thr-1",
                "messages": [
                    _resource(message_id="second", thread_id="thr-1", internal_date="1759400000000"),
                    _resource(message_id="first", thread_id="thr-1", internal_date="1759300000000"),
                ],
            },
        )
        messages = mailer.fetch_thread("thr-1")
        assert [m.message_id for m in messages] == ["first", "second"]
        assert all(m.thread_id == "thr-1" for m in messages)
        session.get.assert_called_once()
        assert session.get.call_args.args[0] == f"{GmailMailer.API}/threads/thr-1"
        assert session.get.call_args.kwargs["params"] == {"format": "full"}

    def test_fetch_thread_without_messages(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.return_value = _response(200, {"id": "thr-1"})
        assert mailer.fetch_thread("thr-1") == []

    def test_fetch_error_raises(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.return_value = _response(404, text="Not Found")
        with pytest.raises(MailerError, match="Gmail 404: Not Found"):
            mailer.fetch_thread("missing")


class TestGmailMailerLabels:
    def test_add_label_reuses_existing_label(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(200, {"id": "thr-1", "labelIds": ["Label_7"]})]
        session.get.return_value = _response(
            200, {"labels": [{"id": "INBOX", "name": "INBOX"}, {"id": "Label_7", "name": "Offerings/Offer"}]}
        )

        mailer.add_label("thr-1", "Offerings/Offer")

        session.get.assert_called_once()
        assert session.get.call_args.args[0] == f"{GmailMailer.API}/labels"
        assert session.post.call_count == 2  # token + modify, no create
        modify = session.post.call_args_list[1]
        assert modify.args[0] == f"{GmailMailer.API}/threads/thr-1/modify"
        assert modify.kwargs["json"] == {"addLabelIds": ["Label_7"]}

    def test_add_label_matches_case_insensitively(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(200, {})]
        session.get.return_value = _response(200, {"labels": [{"id": "Label_1", "name": "offerings/offer"}]})
        mailer.add_label("thr-1", "Offerings/Offer")
        assert session.post.call_count == 2
        assert session.post.call_args_list[1].kwargs["json"] == {"addLabelIds": ["Label_1"]}

    def test_add_label_creates_missing_label(self):
        mailer, session = _gmail()
        session.post.side_effect = [
            _token_response(),
            _response(200, {"id": "Label_42", "name": "Offerings/Needs reply"}),
            _response(200, {"id": "thr-2"}),
        ]
        session.get.return_value = _response(200, {"labels": [{"id": "INBOX", "name": "INBOX"}]})

        mailer.add_label("thr-2", "Offerings/Needs reply")

        create = session.post.call_args_list[1]
        assert create.args[0] == f"{GmailMailer.API}/labels"
        assert create.kwargs["json"] == {
            "name": "Offerings/Needs reply",
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        modify = session.post.call_args_list[2]
        assert modify.args[0] == f"{GmailMailer.API}/threads/thr-2/modify"
        assert modify.kwargs["json"] == {"addLabelIds": ["Label_42"]}

    def test_add_label_caches_label_id(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(200, {}), _response(200, {})]
        session.get.return_value = _response(200, {"labels": [{"id": "Label_7", "name": "Offerings/Offer"}]})
        mailer.add_label("thr-1", "Offerings/Offer")
        mailer.add_label("thr-2", "Offerings/Offer")
        session.get.assert_called_once()
        assert session.post.call_args_list[2].args[0] == f"{GmailMailer.API}/threads/thr-2/modify"

    def test_add_label_tolerates_empty_or_non_json_modify_response(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(204, json_error=True)]
        session.get.return_value = _response(200, {"labels": [{"id": "L", "name": "X"}]})
        mailer.add_label("thr-1", "X")  # no exception

    def test_add_label_tolerates_non_json_200_response(self):
        mailer, session = _gmail()
        session.post.side_effect = [_token_response(), _response(200, json_error=True)]
        session.get.return_value = _response(200, {"labels": [{"id": "L", "name": "X"}]})
        mailer.add_label("thr-1", "X")  # no exception

    def test_add_label_error_raises(self):
        mailer, session = _gmail()
        session.post.return_value = _token_response()
        session.get.return_value = _response(500, text="backend error")
        with pytest.raises(MailerError, match="Gmail 500: backend error"):
            mailer.add_label("thr-1", "Offerings/Offer")

    def test_thread_url_format(self):
        mailer, _ = _gmail()
        assert mailer.thread_url("18c0ffee") == "https://mail.google.com/mail/u/0/#all/18c0ffee"


# --------------------------------------------------------------------------- #
# build_mailer
# --------------------------------------------------------------------------- #


class TestBuildMailer:
    def _settings(self, **overrides) -> Settings:
        data = dict(sender_email=SENDER, sender_name=SENDER_NAME, outbox_dir="my-outbox")
        data.update(overrides)
        return Settings(**data)

    def test_default_policy_builds_dry_run_mailer(self):
        mailer = build_mailer(self._settings())
        assert isinstance(mailer, DryRunMailer)
        assert mailer.outbox_dir is not None
        assert mailer.outbox_dir.name == "my-outbox"
        assert mailer.sender_email == SENDER
        assert mailer.sender_name == SENDER_NAME

    def test_dry_run_with_empty_outbox_dir(self):
        mailer = build_mailer(self._settings(outbox_dir=""))
        assert isinstance(mailer, DryRunMailer)
        assert mailer.outbox_dir is None

    def test_dry_run_even_with_gmail_credentials(self):
        settings = self._settings(gmail_client_id="a", gmail_client_secret="b", gmail_refresh_token="c")
        assert isinstance(build_mailer(settings), DryRunMailer)

    def test_live_with_credentials_builds_gmail_mailer(self):
        settings = self._settings(
            gmail_client_id="cid",
            gmail_client_secret="secret",
            gmail_refresh_token="refresh",
            policy=Policy(live=True),
        )
        mailer = build_mailer(settings)
        assert isinstance(mailer, GmailMailer)
        assert mailer.sender_email == SENDER
        assert mailer.sender_name == SENDER_NAME
        assert mailer._client_id == "cid"
        assert mailer._client_secret == "secret"
        assert mailer._refresh_token == "refresh"

    def test_live_without_credentials_raises(self):
        settings = self._settings(policy=Policy(live=True))
        with pytest.raises(ValueError, match="Live mode requires"):
            build_mailer(settings)

    def test_live_with_partial_credentials_raises(self):
        settings = self._settings(gmail_client_id="cid", policy=Policy(live=True))
        with pytest.raises(ValueError, match="Live mode requires"):
            build_mailer(settings)
