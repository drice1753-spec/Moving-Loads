"""Tests for email_offerings.templates (pure rendering, no I/O)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from email_offerings.config import Policy, Settings
from email_offerings.models import (
    ActionKind,
    ActionRecord,
    Buyer,
    Classification,
    DeliveredQuote,
    InboundMessage,
    Offering,
    ReplyIntent,
    Unit,
)
from email_offerings.parsers import parse_internal_sheet
from email_offerings.templates import (
    DELIVERED_CALL_TO_ACTION,
    MAKE_OFFER_CALL_TO_ACTION,
    UNSUBSCRIBE_FOOTER_TEXT,
    RenderedEmail,
    blast_subject,
    format_price,
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
    short_date,
    signature_block,
    strip_internal_markup,
    text_to_html,
    unsubscribe_footer,
)

WHEN = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)

# Distinctive values that must never show up in customer-facing output.
COST_SENTINEL = 0.4321
NOTES_SENTINEL = "INTERNAL-NOTES-SENTINEL-7781"
SELL_NOTE_SENTINEL = "Suggested Sell Below $1.10 SENTINEL-9912"
DESCRIPTION_SECRET = "SHEET-SECRET-3344"

INTERNAL_DESCRIPTION = (
    f"DELETE RED...FOB: Calhoun, GA...COST FOB: $0.4321/sf...{SELL_NOTE_SENTINEL}..."
    f"BRING BACK ALL FIRM OFFERS {DESCRIPTION_SECRET}...DELETE RED. "
    "5mm/12mil 7x48 SPC vinyl click planks, 23 pallets per truckload.\n"
    "Approximately 24,000 sf per truckload.\n"
    "COST FOB: $0.4321/sf\n"
    "suggested sell: $1.09/sf\n"
    "Ships from Calhoun, GA."
)

CUSTOMER_FORBIDDEN = ("0.4321", "$0.43", NOTES_SENTINEL, SELL_NOTE_SENTINEL, DESCRIPTION_SECRET, "COST FOB", "DELETE RED")


def make_offering(**overrides) -> Offering:
    data = dict(
        id="silver-rustic-oak-spc-2026-10-01",
        title="Silver Rustic Oak SPC Vinyl Click Flooring",
        description=INTERNAL_DESCRIPTION,
        fob_location="Calhoun, GA",
        unit=Unit.SF,
        sell_price=0.99,
        quantity_available="approx 6 truckloads",
        units_per_truckload=24000,
        make_offers=False,
        cost_price=COST_SENTINEL,
        suggested_sell_note=SELL_NOTE_SENTINEL,
        internal_notes=NOTES_SENTINEL,
        created_at=WHEN,
    )
    data.update(overrides)
    return Offering(**data)


def make_settings(**overrides) -> Settings:
    data = dict(
        sender_email="dan@example.com",
        sender_name="Dan Seller",
        company_name="Example Surplus Sales",
        escalation_email="owner@example.com",
    )
    data.update(overrides)
    return Settings(**data)


def make_buyer(**overrides) -> Buyer:
    data = dict(email="jordan@example.com", name="Jordan Buyer", company="Example Flooring Outlet", postal_code="73127")
    data.update(overrides)
    return Buyer(**data)


def make_quote(**overrides) -> DeliveredQuote:
    data = dict(
        offering_id="silver-rustic-oak-spc-2026-10-01",
        origin="Calhoun, GA",
        destination="Oklahoma City, OK",
        distance_miles=770.4,
        rate_per_mile=3.75,
        truckloads=2.0,
        freight_per_truckload=2900.0,
        freight_total=5800.0,
        unit=Unit.SF,
        fob_price_per_unit=0.99,
        delivered_price_per_unit=1.11,
        units_per_truckload=24000.0,
        units_total=48000.0,
        delivered_total=53280.0,
        quoted_at=WHEN,
    )
    data.update(overrides)
    return DeliveredQuote(**data)


def make_inbound(**overrides) -> InboundMessage:
    data = dict(
        message_id="msg-1",
        thread_id="thread-1",
        from_email="jordan@example.com",
        from_name="Jordan Buyer",
        subject="Re: $0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (New 10/1)",
        body_text="I'll take 2 truckloads at $0.85/sf delivered to 73127.\n\nThanks,\nJordan",
        date=WHEN,
    )
    data.update(overrides)
    return InboundMessage(**data)


def make_classification(**overrides) -> Classification:
    data = dict(
        intent=ReplyIntent.FIRM_OFFER,
        confidence=0.9,
        postal_code="73127",
        truckloads=2.0,
        offer_price=0.85,
        summary="Offers $0.85/sf delivered for two truckloads.",
        source="claude",
    )
    data.update(overrides)
    return Classification(**data)


def assert_customer_safe(rendered: RenderedEmail) -> None:
    """Assert no internal data appears in subject, text or html."""
    for haystack in (rendered.subject, rendered.text, rendered.html or ""):
        for forbidden in CUSTOMER_FORBIDDEN:
            assert forbidden not in haystack, f"{forbidden!r} leaked into {haystack!r}"
        assert "delete red" not in haystack.lower()


# --------------------------------------------------------------------------- #
# Formatting primitives
# --------------------------------------------------------------------------- #


class TestFormatPrice:
    """Tests for format_price."""

    @pytest.mark.parametrize(
        "price, unit, expected",
        [
            (0.99, Unit.SF, "$0.99/sf"),
            (11.9, Unit.SY, "$11.90/sy"),
            (45, Unit.EACH, "$45.00/ea"),
            (2.5, Unit.LF, "$2.50/lf"),
            (1250, Unit.PALLET, "$1,250.00/plt"),
            (8500, Unit.TRUCKLOAD, "$8,500/TL"),
            (12345.6, Unit.TRUCKLOAD, "$12,346/TL"),
            (0, Unit.SF, "$0.00/sf"),
        ],
    )
    def test_formats_known_units(self, price, unit, expected):
        assert format_price(price, unit) == expected

    def test_rounds_sub_cent_prices_to_cents(self):
        assert format_price(0.4321, Unit.SF) == "$0.43/sf"
        assert format_price(0.4567, Unit.SY) == "$0.46/sy"

    def test_accepts_unit_strings(self):
        assert format_price(0.6, "SF") == "$0.60/sf"
        assert format_price(8500, "truckload") == "$8,500/TL"

    def test_rejects_unknown_unit_string(self):
        with pytest.raises(ValueError, match="Unknown unit"):
            format_price(1.0, "bushel")


class TestShortDate:
    """Tests for short_date."""

    def test_datetime_without_zero_padding(self):
        assert short_date(WHEN) == "10/1"

    def test_date_object(self):
        assert short_date(date(2026, 1, 5)) == "1/5"

    def test_two_digit_day(self):
        assert short_date(datetime(2026, 12, 25, tzinfo=timezone.utc)) == "12/25"


# --------------------------------------------------------------------------- #
# Subjects
# --------------------------------------------------------------------------- #


class TestBlastSubject:
    """Tests for blast_subject."""

    def test_priced_subject_matches_owner_convention(self):
        assert blast_subject(make_offering(), WHEN) == "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (New 10/1)"

    def test_make_offers_subject(self):
        subject = blast_subject(make_offering(make_offers=True), WHEN)
        assert subject == "MAKE OFFERS: Silver Rustic Oak SPC Vinyl Click Flooring (New 10/1)"
        assert "$" not in subject

    def test_update_suffix(self):
        subject = blast_subject(make_offering(), WHEN, update=True)
        assert subject == "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (New 10/1) - 10/1 update"

    def test_update_with_make_offers(self):
        subject = blast_subject(make_offering(make_offers=True), WHEN, update=True)
        assert subject.startswith("MAKE OFFERS: ")
        assert subject.endswith("(New 10/1) - 10/1 update")

    def test_truckload_unit_uses_whole_dollars(self):
        offering = make_offering(unit=Unit.TRUCKLOAD, sell_price=8500, title="Mixed Porcelain Tile Truckload")
        assert blast_subject(offering, WHEN) == "$8,500/TL Mixed Porcelain Tile Truckload (New 10/1)"

    def test_title_whitespace_is_trimmed(self):
        offering = make_offering(title="  Padded Title  ")
        assert blast_subject(offering, WHEN) == "$0.99/sf Padded Title (New 10/1)"


class TestPersonalSubject:
    """Tests for personal_subject."""

    def test_upper_cases_first_name(self):
        assert personal_subject(make_buyer(), make_offering()) == "JORDAN>>$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring"

    def test_uses_only_first_name(self):
        buyer = make_buyer(name="mary ann smith")
        assert personal_subject(buyer, make_offering()).startswith("MARY>>")

    def test_no_name_falls_back_to_blast_subject_without_date(self):
        buyer = make_buyer(name="")
        assert personal_subject(buyer, make_offering()) == "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring"

    def test_make_offers_keeps_prefix_consistent_with_blast(self):
        subject = personal_subject(make_buyer(), make_offering(make_offers=True))
        assert subject == "JORDAN>>MAKE OFFERS: Silver Rustic Oak SPC Vinyl Click Flooring"

    def test_never_contains_date_suffix(self):
        assert "(New" not in personal_subject(make_buyer(), make_offering())


class TestInternalSubject:
    """Tests for internal_subject."""

    def test_uses_cost_price_when_set(self):
        offering = make_offering(cost_price=0.75)
        assert internal_subject(offering, WHEN) == "$0.75/sf Silver Rustic Oak SPC Vinyl Click Flooring (AWR 10/1)"

    def test_falls_back_to_sell_price(self):
        offering = make_offering(cost_price=None)
        assert internal_subject(offering, WHEN) == "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (AWR 10/1)"

    def test_zero_cost_price_is_respected(self):
        offering = make_offering(cost_price=0.0)
        assert internal_subject(offering, WHEN).startswith("$0.00/sf ")


# --------------------------------------------------------------------------- #
# Internal markup
# --------------------------------------------------------------------------- #


class TestStripInternalMarkup:
    """Tests for strip_internal_markup."""

    def test_removes_single_line_span_with_trailing_period(self):
        text = "DELETE RED...FOB: Calhoun, GA...COST FOB: $0.99/sf...DELETE RED. Nice flooring."
        assert strip_internal_markup(text) == "Nice flooring."

    def test_removes_span_without_trailing_period(self):
        text = "DELETE RED cost stuff DELETE RED Nice flooring."
        assert strip_internal_markup(text) == "Nice flooring."

    def test_case_insensitive(self):
        text = "delete red secret Delete Red. Visible text."
        assert strip_internal_markup(text) == "Visible text."

    def test_span_across_newlines(self):
        text = "Intro line.\nDELETE RED\nCOST FOB: $0.50/sf\nmargin notes\nDELETE RED.\nOutro line."
        result = strip_internal_markup(text)
        assert result == "Intro line.\n\nOutro line."
        assert "margin" not in result

    def test_removes_multiple_spans(self):
        text = "A DELETE RED one DELETE RED. B DELETE RED two DELETE RED C"
        assert strip_internal_markup(text) == "A B C"

    def test_unbalanced_marker_drops_to_end_of_text(self):
        text = "Public intro.\nDELETE RED secret cost $0.50 and more\nstill secret"
        assert strip_internal_markup(text) == "Public intro."

    def test_third_marker_after_a_pair_drops_tail(self):
        text = "Keep. DELETE RED x DELETE RED. Also keep. DELETE RED leaked tail"
        assert strip_internal_markup(text) == "Keep. Also keep."

    def test_preserves_single_spaces_and_indentation_elsewhere(self):
        text = "First line.\nSecond line with  two spaces."
        assert strip_internal_markup(text) == "First line.\nSecond line with two spaces."

    @pytest.mark.parametrize("line", ["COST FOB: $0.99/sf", "cost fob $0.99", "Suggested Sell Below $1.10", "SUGGESTED SELL: $1.09", "  Cost   FOB 0.5"])
    def test_drops_lines_with_internal_keywords(self, line):
        text = f"Keep this.\n{line}\nKeep that."
        assert strip_internal_markup(text) == "Keep this.\nKeep that."

    def test_collapses_blank_line_runs(self):
        text = "One.\n\n\n\n\nTwo."
        assert strip_internal_markup(text) == "One.\n\nTwo."

    def test_strips_surrounding_whitespace(self):
        assert strip_internal_markup("   \n Hello \n  ") == "Hello"

    def test_text_without_markup_is_unchanged(self):
        text = "Plain description.\nSecond line."
        assert strip_internal_markup(text) == text

    def test_empty_and_none_like_inputs(self):
        assert strip_internal_markup("") == ""

    def test_word_boundary_prevents_false_positive(self):
        text = "The DELETE REDUX album is not markup."
        assert strip_internal_markup(text) == text


# --------------------------------------------------------------------------- #
# Shared building blocks
# --------------------------------------------------------------------------- #


class TestUnsubscribeFooter:
    """Tests for unsubscribe_footer."""

    def test_enabled_by_default(self):
        assert unsubscribe_footer(make_settings()) == UNSUBSCRIBE_FOOTER_TEXT
        assert unsubscribe_footer(make_settings()) == "Reply with REMOVE to stop receiving these offerings."

    def test_disabled_by_policy(self):
        settings = make_settings(policy=Policy(unsubscribe_footer=False))
        assert unsubscribe_footer(settings) == ""


class TestSignatureBlock:
    """Tests for signature_block."""

    def test_explicit_signature_wins(self):
        settings = make_settings(signature="  Dan Seller\n555-0100 (example)  ")
        assert signature_block(settings) == "Dan Seller\n555-0100 (example)"

    def test_default_is_dash_name_and_company(self):
        assert signature_block(make_settings()) == "-Dan Seller\nExample Surplus Sales"

    def test_without_company(self):
        assert signature_block(make_settings(company_name="")) == "-Dan Seller"

    def test_without_name_uses_sender_email(self):
        settings = make_settings(sender_name="", company_name="")
        assert signature_block(settings) == "-dan@example.com"


class TestTextToHtml:
    """Tests for text_to_html."""

    def test_paragraphs_from_blank_lines(self):
        assert text_to_html("One.\n\nTwo.") == "<p>One.</p>\n<p>Two.</p>"

    def test_newlines_inside_paragraph_become_br(self):
        assert text_to_html("Line 1\nLine 2") == "<p>Line 1<br>Line 2</p>"

    def test_escapes_dynamic_values(self):
        html = text_to_html('5 < 6 & "quotes" <script>')
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&amp;" in html
        assert "&quot;quotes&quot;" in html

    def test_blank_runs_and_surrounding_whitespace_ignored(self):
        assert text_to_html("\n\nA\n\n\n\nB\n\n") == "<p>A</p>\n<p>B</p>"

    def test_empty_text(self):
        assert text_to_html("") == ""


# --------------------------------------------------------------------------- #
# Customer-facing renderers
# --------------------------------------------------------------------------- #


class TestRenderOfferingEmail:
    """Tests for render_offering_email."""

    def test_blast_subject_and_body_parts(self):
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN)
        assert rendered.subject == "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (New 10/1)"
        assert "5mm/12mil 7x48 SPC vinyl click planks" in rendered.text
        assert "Approximately 24,000 sf per truckload." in rendered.text
        assert "Ships from Calhoun, GA." in rendered.text
        assert "FOB: Calhoun, GA" in rendered.text
        assert "Price: $0.99/sf FOB Calhoun, GA" in rendered.text
        assert "Quantity: approx 6 truckloads" in rendered.text
        assert rendered.text.rstrip().endswith(UNSUBSCRIBE_FOOTER_TEXT)

    def test_blast_has_no_greeting(self):
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN)
        assert not rendered.text.startswith("Hi ")
        assert "Hello," not in rendered.text

    def test_order_description_before_facts_before_signature(self):
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN)
        text = rendered.text
        assert text.index("SPC vinyl click planks") < text.index("FOB: Calhoun, GA") < text.index("-Dan Seller") < text.index(UNSUBSCRIBE_FOOTER_TEXT)

    def test_make_offers_adds_call_to_action(self):
        rendered = render_offering_email(make_offering(make_offers=True), make_settings(), when=WHEN)
        assert rendered.subject.startswith("MAKE OFFERS: ")
        assert MAKE_OFFER_CALL_TO_ACTION in rendered.text
        assert "make a firm offer" in rendered.text

    def test_no_call_to_action_without_make_offers(self):
        rendered = render_offering_email(make_offering(make_offers=False), make_settings(), when=WHEN)
        assert MAKE_OFFER_CALL_TO_ACTION not in rendered.text

    def test_quantity_line_omitted_when_blank(self):
        rendered = render_offering_email(make_offering(quantity_available="  "), make_settings(), when=WHEN)
        assert "Quantity:" not in rendered.text

    def test_default_signature_with_company(self):
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN)
        assert "-Dan Seller\nExample Surplus Sales" in rendered.text

    def test_custom_signature(self):
        settings = make_settings(signature="Dan Seller | Example Surplus | 555-0100")
        rendered = render_offering_email(make_offering(), settings, when=WHEN)
        assert "Dan Seller | Example Surplus | 555-0100" in rendered.text
        assert "-Dan Seller\n" not in rendered.text

    def test_footer_omitted_when_policy_disables_it(self):
        settings = make_settings(policy=Policy(unsubscribe_footer=False))
        rendered = render_offering_email(make_offering(), settings, when=WHEN)
        assert UNSUBSCRIBE_FOOTER_TEXT not in rendered.text
        assert rendered.text.rstrip().endswith("Example Surplus Sales")

    def test_personal_variant_subject_greeting_and_note_first(self):
        note = "I can deliver this truckload at $0.99/sf. Pretty great deal. -Dan"
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN, buyer=make_buyer(), personal_note=note)
        assert rendered.subject == "JORDAN>>$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring"
        assert rendered.text.startswith("Hi Jordan,\n\n" + note + "\n\n")
        assert rendered.text.index(note) < rendered.text.index("SPC vinyl click planks")

    def test_personal_variant_without_name(self):
        buyer = make_buyer(name="")
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN, buyer=buyer)
        assert rendered.subject == "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring"
        assert rendered.text.startswith("Hello,\n\n")

    def test_personal_note_without_buyer_still_appears(self):
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN, personal_note="Note first.")
        assert rendered.text.startswith("Note first.\n\n")

    def test_update_variant(self):
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN, update=True)
        assert rendered.subject.endswith("(New 10/1) - 10/1 update")
        assert "Update 10/1: this material is still available." in rendered.text

    def test_html_is_escaped_paragraphs(self):
        offering = make_offering(description="Tile & stone <b>special</b>\nSecond line")
        rendered = render_offering_email(offering, make_settings(), when=WHEN)
        assert rendered.html is not None
        assert rendered.html.startswith("<p>")
        assert "Tile &amp; stone &lt;b&gt;special&lt;/b&gt;<br>Second line" in rendered.html
        assert "<b>" not in rendered.html

    def test_truckload_pricing(self):
        offering = make_offering(unit=Unit.TRUCKLOAD, sell_price=8500, description="Mixed load.")
        rendered = render_offering_email(offering, make_settings(), when=WHEN)
        assert "Price: $8,500/TL FOB Calhoun, GA" in rendered.text

    def test_never_leaks_internal_data(self):
        for kwargs in ({}, {"buyer": make_buyer(), "personal_note": "Great deal."}, {"update": True}):
            rendered = render_offering_email(make_offering(make_offers=True), make_settings(), when=WHEN, **kwargs)
            assert_customer_safe(rendered)

    def test_unbalanced_markup_in_description_is_dropped(self):
        offering = make_offering(description=f"Public text. DELETE RED cost 0.4321 {NOTES_SENTINEL}")
        rendered = render_offering_email(offering, make_settings(), when=WHEN)
        assert "Public text." in rendered.text
        assert_customer_safe(rendered)


class TestRenderDeliveredQuoteReply:
    """Tests for render_delivered_quote_reply."""

    def test_subject(self):
        rendered = render_delivered_quote_reply(make_offering(), make_quote(), make_buyer(), make_settings())
        assert rendered.subject == "Delivered pricing: Silver Rustic Oak SPC Vinyl Click Flooring"

    def test_mentions_all_quote_facts(self):
        rendered = render_delivered_quote_reply(make_offering(), make_quote(), make_buyer(), make_settings())
        text = rendered.text
        assert text.startswith("Hi Jordan,")
        assert "freight is $5,800 from Calhoun, GA to Oklahoma City, OK (770 mi)" in text
        assert "on 2 truckloads" in text
        assert "$2,900 per truckload" in text
        assert "FOB price: $0.99/sf" in text
        assert "Delivered price: approx. $1.11/sf" in text
        assert "Total: 48,000 sf for about $53,280 delivered" in text
        assert "make a firm offer delivered to your location" in text
        assert DELIVERED_CALL_TO_ACTION in text
        assert text.rstrip().endswith(UNSUBSCRIBE_FOOTER_TEXT)

    def test_single_truckload_wording(self):
        quote = make_quote(truckloads=1.0, freight_total=2900.0, units_total=24000.0, delivered_total=26640.0)
        text = render_delivered_quote_reply(make_offering(), quote, make_buyer(), make_settings()).text
        assert "on 1 truckload." in text
        assert "per truckload" not in text

    def test_fractional_truckloads(self):
        quote = make_quote(truckloads=1.5, freight_total=4350.0, units_total=36000.0, delivered_total=39960.0)
        text = render_delivered_quote_reply(make_offering(), quote, make_buyer(), make_settings()).text
        assert "on 1.5 truckloads" in text

    def test_truckload_unit_has_no_total_line(self):
        offering = make_offering(unit=Unit.TRUCKLOAD, sell_price=8500, units_per_truckload=None)
        quote = make_quote(
            unit=Unit.TRUCKLOAD, fob_price_per_unit=8500, delivered_price_per_unit=11400,
            units_per_truckload=None, units_total=2.0, delivered_total=22800.0,
        )
        text = render_delivered_quote_reply(offering, quote, make_buyer(), make_settings()).text
        assert "Delivered price: approx. $11,400/TL" in text
        assert "Total:" not in text

    def test_total_without_delivered_total(self):
        quote = make_quote(delivered_total=None)
        text = render_delivered_quote_reply(make_offering(), quote, make_buyer(), make_settings()).text
        assert "Total: 48,000 sf\n\n" in text
        assert "for about" not in text

    def test_no_units_total_omits_total_line(self):
        quote = make_quote(units_total=None, delivered_total=None)
        text = render_delivered_quote_reply(make_offering(), quote, make_buyer(), make_settings()).text
        assert "Total:" not in text

    def test_buyer_none_uses_generic_greeting(self):
        text = render_delivered_quote_reply(make_offering(), make_quote(), None, make_settings()).text
        assert text.startswith("Hello,")

    def test_footer_respects_policy(self):
        settings = make_settings(policy=Policy(unsubscribe_footer=False))
        text = render_delivered_quote_reply(make_offering(), make_quote(), make_buyer(), settings).text
        assert UNSUBSCRIBE_FOOTER_TEXT not in text

    def test_never_leaks_internal_data(self):
        rendered = render_delivered_quote_reply(make_offering(), make_quote(), make_buyer(), make_settings())
        assert_customer_safe(rendered)


class TestRenderZipRequestReply:
    """Tests for render_zip_request_reply."""

    def test_asks_for_zip_and_truckloads(self):
        rendered = render_zip_request_reply(make_offering(), make_buyer(), make_settings())
        assert rendered.subject == "Delivered pricing: Silver Rustic Oak SPC Vinyl Click Flooring"
        assert rendered.text.startswith("Hi Jordan,")
        assert "ZIP code" in rendered.text
        assert "how many truckloads" in rendered.text
        assert "$0.99/sf FOB Calhoun, GA" in rendered.text
        assert UNSUBSCRIBE_FOOTER_TEXT in rendered.text
        assert rendered.html and "<p>" in rendered.html

    def test_buyer_none(self):
        rendered = render_zip_request_reply(make_offering(), None, make_settings())
        assert rendered.text.startswith("Hello,")

    def test_never_leaks_internal_data(self):
        assert_customer_safe(render_zip_request_reply(make_offering(), make_buyer(), make_settings()))


class TestRenderInterestedReply:
    """Tests for render_interested_reply."""

    def test_includes_details_and_asks_for_zip_and_quantity(self):
        rendered = render_interested_reply(make_offering(), make_buyer(), make_settings())
        assert rendered.subject == "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring"
        assert "Thanks for your interest in the Silver Rustic Oak SPC Vinyl Click Flooring" in rendered.text
        assert "5mm/12mil 7x48 SPC vinyl click planks" in rendered.text
        assert "FOB: Calhoun, GA" in rendered.text
        assert "Price: $0.99/sf FOB Calhoun, GA" in rendered.text
        assert "Quantity: approx 6 truckloads" in rendered.text
        assert "ZIP code" in rendered.text
        assert "how many truckloads" in rendered.text
        assert "-Dan Seller" in rendered.text
        assert UNSUBSCRIBE_FOOTER_TEXT in rendered.text

    def test_make_offers_adds_call_to_action(self):
        rendered = render_interested_reply(make_offering(make_offers=True), make_buyer(), make_settings())
        assert rendered.subject.startswith("MAKE OFFERS: ")
        assert MAKE_OFFER_CALL_TO_ACTION in rendered.text

    def test_no_call_to_action_otherwise(self):
        rendered = render_interested_reply(make_offering(), make_buyer(), make_settings())
        assert MAKE_OFFER_CALL_TO_ACTION not in rendered.text

    def test_never_leaks_internal_data(self):
        assert_customer_safe(render_interested_reply(make_offering(make_offers=True), make_buyer(), make_settings()))
        assert_customer_safe(render_interested_reply(make_offering(), None, make_settings()))


class TestRenderFollowUp:
    """Tests for render_follow_up."""

    def test_short_still_available_nudge(self):
        rendered = render_follow_up(make_offering(), make_buyer(), make_settings(), when=WHEN)
        assert rendered.subject == "Still available: $0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring"
        assert rendered.text.startswith("Hi Jordan,")
        assert "still available as of 10/1" in rendered.text
        assert "$0.99/sf FOB Calhoun, GA" in rendered.text
        assert "approx 6 truckloads available." in rendered.text
        assert "ZIP code" in rendered.text
        assert UNSUBSCRIBE_FOOTER_TEXT in rendered.text

    def test_is_short(self):
        rendered = render_follow_up(make_offering(), make_buyer(), make_settings(), when=WHEN)
        assert "SPC vinyl click planks" not in rendered.text  # no full description
        assert len(rendered.text) < 600

    def test_without_quantity(self):
        rendered = render_follow_up(make_offering(quantity_available=""), None, make_settings(), when=WHEN)
        assert "still available as of 10/1 at $0.99/sf FOB Calhoun, GA." in rendered.text
        assert "available." not in rendered.text
        assert rendered.text.startswith("Hello,")

    def test_never_leaks_internal_data(self):
        assert_customer_safe(render_follow_up(make_offering(), make_buyer(), make_settings(), when=WHEN))


class TestRenderOfferAcknowledgement:
    """Tests for render_offer_acknowledgement."""

    def test_acknowledges_without_accepting(self):
        rendered = render_offer_acknowledgement(make_offering(), make_buyer(), make_settings())
        assert rendered.subject == "Your offer: Silver Rustic Oak SPC Vinyl Click Flooring"
        assert rendered.text.startswith("Hi Jordan,")
        assert "Got your offer" in rendered.text
        assert "confirm shortly" in rendered.text
        assert "accept" not in rendered.text.lower()
        assert "deal" not in rendered.text.lower()
        assert UNSUBSCRIBE_FOOTER_TEXT in rendered.text

    def test_buyer_none(self):
        rendered = render_offer_acknowledgement(make_offering(), None, make_settings())
        assert rendered.text.startswith("Hello,")

    def test_never_leaks_internal_data(self):
        assert_customer_safe(render_offer_acknowledgement(make_offering(), make_buyer(), make_settings()))


class TestCustomerFacingInvariant:
    """Safety invariant 2: no customer-facing renderer leaks internal data (any settings)."""

    @pytest.mark.parametrize("make_offers", [False, True])
    @pytest.mark.parametrize("footer", [False, True])
    def test_all_customer_renderers_are_safe(self, make_offers, footer):
        offering = make_offering(make_offers=make_offers)
        settings = make_settings(policy=Policy(unsubscribe_footer=footer))
        buyer = make_buyer()
        rendered_all = [
            render_offering_email(offering, settings, when=WHEN),
            render_offering_email(offering, settings, when=WHEN, buyer=buyer, personal_note="Quick note"),
            render_offering_email(offering, settings, when=WHEN, update=True),
            render_follow_up(offering, buyer, settings, when=WHEN),
            render_delivered_quote_reply(offering, make_quote(), buyer, settings),
            render_zip_request_reply(offering, buyer, settings),
            render_interested_reply(offering, buyer, settings),
            render_offer_acknowledgement(offering, buyer, settings),
        ]
        for rendered in rendered_all:
            assert isinstance(rendered, RenderedEmail)
            assert rendered.subject and rendered.text and rendered.html
            assert_customer_safe(rendered)
            assert (UNSUBSCRIBE_FOOTER_TEXT in rendered.text) is footer


# --------------------------------------------------------------------------- #
# Owner / sales-team facing renderers
# --------------------------------------------------------------------------- #


class TestRenderInternalSheet:
    """Tests for render_internal_sheet."""

    def test_subject_uses_awr_convention(self):
        rendered = render_internal_sheet(make_offering(), make_settings(), when=WHEN)
        assert rendered.subject == "$0.43/sf Silver Rustic Oak SPC Vinyl Click Flooring (AWR 10/1)"

    def test_delete_red_header_contents_and_order(self):
        rendered = render_internal_sheet(make_offering(make_offers=True), make_settings(), when=WHEN)
        header = rendered.text.split("\n\n")[0]
        assert header.startswith("DELETE RED...FOB: Calhoun, GA...COST FOB: $0.43/sf...SELL: $0.99/sf...")
        assert SELL_NOTE_SENTINEL in header
        assert "approx 6 truckloads available" in header
        assert NOTES_SENTINEL in header
        assert "...BRING BACK ALL FIRM OFFERS...DELETE RED." in header
        assert header.endswith("DELETE RED.")
        assert header.index("COST FOB") < header.index("SELL:") < header.index(SELL_NOTE_SENTINEL) < header.index("BRING BACK")

    def test_description_follows_header_without_its_own_markup(self):
        rendered = render_internal_sheet(make_offering(), make_settings(), when=WHEN)
        header, description = rendered.text.split("\n\n")[:2]
        assert "5mm/12mil 7x48 SPC vinyl click planks" in description
        assert DESCRIPTION_SECRET not in description  # markup inside the description was stripped
        assert rendered.text.count("DELETE RED") == 2

    def test_bring_back_only_when_make_offers(self):
        rendered = render_internal_sheet(make_offering(make_offers=False), make_settings(), when=WHEN)
        assert "BRING BACK ALL FIRM OFFERS" not in rendered.text

    def test_without_cost_price_omits_cost_fob(self):
        offering = make_offering(cost_price=None, suggested_sell_note="", internal_notes="", quantity_available="")
        rendered = render_internal_sheet(offering, make_settings(), when=WHEN)
        assert rendered.subject.startswith("$0.99/sf ")
        assert "COST FOB" not in rendered.text
        assert rendered.text.startswith("DELETE RED...FOB: Calhoun, GA...SELL: $0.99/sf...DELETE RED.")

    def test_has_facts_and_signature_but_no_unsubscribe_footer(self):
        rendered = render_internal_sheet(make_offering(), make_settings(), when=WHEN)
        assert "FOB: Calhoun, GA" in rendered.text
        assert "Price: $0.99/sf FOB Calhoun, GA" in rendered.text
        assert "-Dan Seller" in rendered.text
        assert UNSUBSCRIBE_FOOTER_TEXT not in rendered.text

    def test_strip_internal_markup_removes_the_rendered_header(self):
        rendered = render_internal_sheet(make_offering(make_offers=True), make_settings(), when=WHEN)
        cleaned = strip_internal_markup(rendered.text)
        assert "DELETE RED" not in cleaned
        assert "COST FOB" not in cleaned
        assert NOTES_SENTINEL not in cleaned
        assert "5mm/12mil 7x48 SPC vinyl click planks" in cleaned

    def test_html_is_escaped(self):
        rendered = render_internal_sheet(make_offering(internal_notes="margin <tight> & thin"), make_settings(), when=WHEN)
        assert "margin &lt;tight&gt; &amp; thin" in (rendered.html or "")


    def test_quantity_already_marked_available_is_not_doubled(self):
        offering = make_offering(quantity_available="Approx 6 truckloads available")
        header = render_internal_sheet(offering, make_settings(), when=WHEN).text.split("\n\n")[0]
        assert "...Approx 6 truckloads available..." in header
        assert "available available" not in header

    def test_quantity_without_marker_gets_available_suffix(self):
        header = render_internal_sheet(make_offering(), make_settings(), when=WHEN).text.split("\n\n")[0]
        assert "...approx 6 truckloads available..." in header

    def test_notes_holding_a_captured_span_are_not_duplicated(self):
        span = (
            "DELETE RED...FOB: Calhoun, GA...COST FOB: $0.4321/sf...SELL: $0.99/sf..."
            f"{SELL_NOTE_SENTINEL}...approx 6 truckloads available...Mill is closing out this color..."
            "BRING BACK ALL FIRM OFFERS...DELETE RED."
        )
        offering = make_offering(internal_notes=span, make_offers=False)
        header = render_internal_sheet(offering, make_settings(), when=WHEN).text.split("\n\n")[0]
        assert header.count("DELETE RED") == 2
        assert header.count("FOB:") == 2  # "FOB:" and "COST FOB:" once each
        assert header.count("SELL:") == 1
        assert header.count(SELL_NOTE_SENTINEL) == 1
        assert header.count("approx 6 truckloads available") == 1
        assert "Mill is closing out this color" in header
        assert "BRING BACK" not in header  # the structured make_offers=False wins over stale notes

    def test_parsed_sheet_re_renders_as_the_same_sheet(self):
        sheet = (
            "DELETE RED...FOB: Calhoun, GA...COST FOB: $0.84/sf...SELL: $0.99/sf..."
            "Suggested Sell Below $1.09/sf delivered...Mill is closing out this color..."
            "BRING BACK ALL FIRM OFFERS...DELETE RED.\n\n"
            "First quality planks on original pallets. Approx 6 truckloads available."
        )
        first = parse_internal_sheet("$0.99/sf Silver Rustic Oak SPC (AWR 10/1)", sheet, now=WHEN)
        assert "DELETE RED" in first.internal_notes  # the parser keeps the whole span
        rendered = render_internal_sheet(first, make_settings(), when=WHEN)
        header = rendered.text.split("\n\n")[0]
        assert header.count("DELETE RED") == 2
        assert header.count("COST FOB") == 1 and header.count("SELL:") == 1 and header.count("BRING BACK") == 1
        assert "Mill is closing out this color" in header
        assert "available available" not in rendered.text
        again = parse_internal_sheet(rendered.subject, rendered.text, now=WHEN)
        assert (again.sell_price, again.cost_price, again.fob_location, again.unit, again.make_offers) == (
            0.99,
            0.84,
            "Calhoun, GA",
            Unit.SF,
            True,
        )
        assert again.suggested_sell_note == first.suggested_sell_note
        assert again.quantity_available == first.quantity_available
        assert render_internal_sheet(again, make_settings(), when=WHEN).text.split("\n\n")[0] == header


class TestRenderOfferEscalation:
    """Tests for render_offer_escalation."""

    def test_subject_identifies_offering_and_buyer(self):
        rendered = render_offer_escalation(make_offering(), make_inbound(), make_classification(), make_settings())
        assert rendered.subject == "FIRM OFFER: Silver Rustic Oak SPC Vinyl Click Flooring - jordan@example.com"

    def test_body_has_who_what_summary_quote(self):
        rendered = render_offer_escalation(
            make_offering(), make_inbound(), make_classification(), make_settings(), thread_url="https://mail.example.com/thread-1"
        )
        text = rendered.text
        assert "NOT been accepted" in text
        assert "Buyer: Jordan Buyer <jordan@example.com>" in text
        assert "Offering: silver-rustic-oak-spc-2026-10-01" in text
        assert "Asking: $0.99/sf FOB Calhoun, GA" in text
        assert "Offer price: $0.85/sf" in text
        assert "Quantity: 2 truckloads" in text
        assert "Destination: 73127" in text
        assert "Classification: firm offer (claude, confidence 0.90)" in text
        assert "Summary: Offers $0.85/sf delivered for two truckloads." in text
        assert "Thread: https://mail.example.com/thread-1" in text
        assert "Original message from jordan@example.com on 2026-10-01T14:30:00+00:00 (subject: Re: $0.99/sf Silver" in text
        assert "> I'll take 2 truckloads at $0.85/sf delivered to 73127.\n> \n> Thanks,\n> Jordan" in text

    def test_every_quoted_line_is_prefixed(self):
        rendered = render_offer_escalation(make_offering(), make_inbound(), make_classification(), make_settings())
        quoted = rendered.text.split("Original message", 1)[1].split("\n")[1:]
        assert quoted and all(line.startswith("> ") for line in quoted)

    def test_optional_fields_omitted_when_absent(self):
        classification = make_classification(postal_code=None, destination=None, truckloads=None, offer_price=None, summary="  ", source="rules")
        rendered = render_offer_escalation(make_offering(), make_inbound(from_name=""), classification, make_settings())
        text = rendered.text
        assert "Buyer: jordan@example.com" in text
        assert "<" not in text.split("Original message")[0]
        for label in ("Offer price:", "Quantity:", "Units:", "Destination:", "Summary:", "Thread:"):
            assert label not in text
        assert "(rules, confidence" in text

    def test_destination_falls_back_to_city_state(self):
        classification = make_classification(postal_code=None, destination="Oklahoma City, OK")
        text = render_offer_escalation(make_offering(), make_inbound(), classification, make_settings()).text
        assert "Destination: Oklahoma City, OK" in text

    def test_quantity_units_with_offering_unit(self):
        classification = make_classification(truckloads=None, quantity_units=48000.0)
        text = render_offer_escalation(make_offering(), make_inbound(), classification, make_settings()).text
        assert "Units: 48,000 sf" in text
        assert "Quantity:" not in text

    def test_truckload_offering_formats_offer_price_whole_dollars(self):
        offering = make_offering(unit=Unit.TRUCKLOAD, sell_price=8500, units_per_truckload=None)
        classification = make_classification(offer_price=8000.0, truckloads=1.0)
        text = render_offer_escalation(offering, make_inbound(), classification, make_settings()).text
        assert "Offer price: $8,000/TL" in text
        assert "Quantity: 1 truckload\n" in text

    def test_unknown_offering(self):
        classification = make_classification(quantity_units=10.0)
        rendered = render_offer_escalation(None, make_inbound(), classification, make_settings())
        assert rendered.subject == "FIRM OFFER: unknown offering - jordan@example.com"
        assert "Offering:" not in rendered.text
        assert "Asking:" not in rendered.text
        assert "Offer price: $0.85" in rendered.text
        assert "Units: 10\n" in rendered.text

    def test_empty_body_is_quoted_as_placeholder(self):
        rendered = render_offer_escalation(make_offering(), make_inbound(body_text="   "), make_classification(), make_settings())
        assert "> (empty message)" in rendered.text

    def test_html_escapes_quoted_body(self):
        inbound = make_inbound(body_text="<b>bold</b> offer & more")
        rendered = render_offer_escalation(make_offering(), inbound, make_classification(), make_settings())
        assert "&gt; &lt;b&gt;bold&lt;/b&gt; offer &amp; more" in (rendered.html or "")


class TestRenderDigest:
    """Tests for render_digest."""

    def _actions(self) -> list[ActionRecord]:
        oid = "silver-rustic-oak-spc-2026-10-01"
        return [
            ActionRecord(kind=ActionKind.BLAST_SENT, at=WHEN, offering_id=oid, details="50 recipients"),
            ActionRecord(kind=ActionKind.BLAST_SENT, at=WHEN, offering_id=oid, details="50 recipients"),
            ActionRecord(kind=ActionKind.QUOTE_SENT, at=WHEN, offering_id=oid, buyer_email="pat@example.com"),
            ActionRecord(kind=ActionKind.OFFER_ESCALATED, at=WHEN, offering_id=oid, buyer_email="Jordan@Example.com", details="$0.85/sf x 2 TL"),
            ActionRecord(kind=ActionKind.DRAFT_CREATED, at=WHEN, offering_id=oid, buyer_email="sam@example.com", details="question"),
            ActionRecord(kind=ActionKind.DRAFT_CREATED, at=WHEN, offering_id="", buyer_email="", details="unknown sender"),
            ActionRecord(kind=ActionKind.ERROR, at=WHEN, details="quote failed: no API key"),
        ]

    def test_subject_same_day(self):
        rendered = render_digest([], make_settings(), since=WHEN, until=WHEN)
        assert rendered.subject == "Offerings digest 10/1"

    def test_subject_spanning_days(self):
        since = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
        rendered = render_digest([], make_settings(), since=since, until=WHEN)
        assert rendered.subject == "Offerings digest 9/28 - 10/1"

    def test_window_mode_and_counts_by_kind(self):
        since = datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)
        rendered = render_digest(self._actions(), make_settings(), since=since, until=WHEN, pending_drafts=4)
        text = rendered.text
        assert "Offerings digest [DRY RUN] for 2026-09-30T14:30+00:00 to 2026-10-01T14:30+00:00." in text
        assert "7 action(s) recorded." in text
        assert "Actions by kind:" in text
        assert "  blast_sent: 2" in text
        assert "  quote_sent: 1" in text
        assert "  offer_escalated: 1" in text
        assert "  draft_created: 2" in text
        assert "  error: 1" in text
        assert "personal_sent" not in text

    def test_live_mode_label(self):
        settings = make_settings(policy=Policy(live=True))
        text = render_digest([], settings, since=WHEN, until=WHEN).text
        assert "[LIVE]" in text

    def test_escalated_offers_and_drafts_listed_with_buyer_and_offering(self):
        text = render_digest(self._actions(), make_settings(), since=WHEN, until=WHEN, pending_drafts=4).text
        assert "Escalated offers (1):\n  - jordan@example.com - silver-rustic-oak-spc-2026-10-01: $0.85/sf x 2 TL" in text
        assert "Drafts created in this window (2):" in text
        assert "  - sam@example.com - silver-rustic-oak-spc-2026-10-01: question" in text
        assert "  - (unknown buyer) - (no offering): unknown sender" in text
        assert "Drafts still awaiting review: 4" in text

    def test_errors_section(self):
        text = render_digest(self._actions(), make_settings(), since=WHEN, until=WHEN).text
        assert "Errors (1):\n  - (unknown buyer) - (no offering): quote failed: no API key" in text

    def test_empty_digest(self):
        rendered = render_digest([], make_settings(), since=WHEN, until=WHEN)
        text = rendered.text
        assert "0 action(s) recorded." in text
        assert "Actions by kind:" not in text
        assert "Escalated offers (0):\n  (none)" in text
        assert "Drafts created in this window (0):\n  (none)" in text
        assert "Drafts still awaiting review: 0" in text
        assert "Errors" not in text
        assert rendered.html and rendered.html.startswith("<p>")

    def test_html_escapes_details(self):
        actions = [ActionRecord(kind=ActionKind.ERROR, at=WHEN, details="Gmail 500: <html>boom & bust</html>")]
        rendered = render_digest(actions, make_settings(), since=WHEN, until=WHEN)
        assert "&lt;html&gt;boom &amp; bust&lt;/html&gt;" in (rendered.html or "")
        assert "<html>" not in (rendered.html or "")


class TestRenderedEmail:
    """Tests for the RenderedEmail dataclass."""

    def test_defaults(self):
        rendered = RenderedEmail(subject="S", text="T")
        assert rendered.html is None
        assert (rendered.subject, rendered.text) == ("S", "T")

    def test_renderers_always_fill_html(self):
        rendered = render_offering_email(make_offering(), make_settings(), when=WHEN)
        assert rendered.html == text_to_html(rendered.text)
