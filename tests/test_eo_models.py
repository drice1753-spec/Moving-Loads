"""Tests for email_offerings.models (validation, coercion and serialisation)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

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
    Unit,
    _dump_dt,
    _parse_dt,
    make_offering_id,
    utcnow,
)

NOW = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)


def make_offering(**overrides) -> Offering:
    data = dict(
        id="silver-rustic-oak-spc-2026-10-01",
        title="Silver Rustic Oak SPC Vinyl Click Flooring",
        description="First quality planks on original pallets.",
        fob_location="Calhoun, GA",
        unit=Unit.SF,
        sell_price=0.99,
        created_at=NOW,
    )
    data.update(overrides)
    return Offering(**data)


def make_quote(**overrides) -> DeliveredQuote:
    data = dict(
        offering_id="silver-rustic-oak-spc-2026-10-01",
        origin="Calhoun, GA",
        destination="73127, USA",
        distance_miles=790.0,
        rate_per_mile=3.67,
        truckloads=1.0,
        freight_per_truckload=2899.0,
        freight_total=2899.0,
        unit=Unit.SF,
        fob_price_per_unit=0.99,
        delivered_price_per_unit=1.13,
    )
    data.update(overrides)
    return DeliveredQuote(**data)


class TestHelpers:
    """utcnow, make_offering_id and the datetime helpers."""

    def test_utcnow_is_aware_utc(self):
        assert utcnow().tzinfo is timezone.utc

    def test_make_offering_id_is_a_dated_slug(self):
        offering_id = make_offering_id("5mm/12mil Silver Rustic Oak SPC Vinyl Click Flooring!!", NOW)
        slug, suffix = offering_id[: -len("-2026-10-01")], offering_id[-len("2026-10-01") :]
        assert suffix == "2026-10-01"
        assert slug.startswith("5mm-12mil-silver-rustic-oak")
        assert len(slug) <= 48 and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)

    def test_make_offering_id_falls_back_for_empty_titles(self):
        assert make_offering_id("", NOW) == "offering-2026-10-01"
        assert make_offering_id("!!!", NOW) == "offering-2026-10-01"

    def test_parse_dt_variants(self):
        assert _parse_dt(None) is None and _parse_dt("") is None
        assert _parse_dt(NOW) is NOW
        naive = datetime(2026, 10, 1, 14, 30)
        assert _parse_dt(naive) == NOW
        assert _parse_dt("2026-10-01T14:30:00") == NOW
        assert _parse_dt("2026-10-01T14:30:00+00:00") == NOW

    def test_dump_dt(self):
        assert _dump_dt(None) is None
        assert _dump_dt(NOW) == "2026-10-01T14:30:00+00:00"


class TestUnit:
    """Unit.parse aliases."""

    @pytest.mark.parametrize(
        "text, unit",
        [
            ("sf", Unit.SF),
            ("/SF", Unit.SF),
            ("sq ft", Unit.SF),
            ("square foot", Unit.SF),
            ("sy", Unit.SY),
            ("lf", Unit.LF),
            ("each", Unit.EACH),
            ("pc.", Unit.EACH),
            ("pallet", Unit.PALLET),
            ("skid", Unit.PALLET),
            ("TL", Unit.TRUCKLOAD),
            ("truckload", Unit.TRUCKLOAD),
            ("load", Unit.TRUCKLOAD),
        ],
    )
    def test_aliases(self, text, unit):
        assert Unit.parse(text) is unit

    def test_unknown_unit_raises(self):
        with pytest.raises(ValueError, match="Unknown unit"):
            Unit.parse("bushel")


class TestOffering:
    """Offering validation and serialisation."""

    @pytest.mark.parametrize(
        "overrides, match",
        [
            ({"id": ""}, "id"),
            ({"id": "   "}, "id"),
            ({"title": ""}, "title"),
            ({"fob_location": "  "}, "FOB location"),
            ({"sell_price": -0.01}, "Sell price"),
            ({"cost_price": -1.0}, "Cost price"),
            ({"units_per_truckload": 0}, "Units per truckload"),
        ],
    )
    def test_validation_errors(self, overrides, match):
        with pytest.raises(ValueError, match=match):
            make_offering(**overrides)

    def test_string_unit_and_status_are_coerced(self):
        offering = make_offering(unit="sf", status="active")
        assert offering.unit is Unit.SF
        assert offering.status is OfferingStatus.ACTIVE
        assert offering.is_active

    def test_to_dict_from_dict_round_trip(self):
        offering = make_offering(
            quantity_available="approx 6 truckloads",
            units_per_truckload=20000,
            make_offers=True,
            status=OfferingStatus.ACTIVE,
            expires_at=NOW + timedelta(days=30),
            cost_price=0.84,
            suggested_sell_note="Suggested Sell Below $1.09/sf",
            internal_notes="margin notes",
            tags=["flooring", "spc"],
            attachments=["/tmp/spec.pdf"],
        )
        data = offering.to_dict()
        assert data["unit"] == "sf" and data["status"] == "active"
        assert data["expires_at"] == (NOW + timedelta(days=30)).isoformat()
        assert Offering.from_dict(data) == offering

    def test_from_dict_defaults_and_unknown_keys(self):
        offering = Offering.from_dict(
            {
                "id": "x-2026-10-01",
                "title": "Pavers",
                "description": "",
                "fob_location": "Dalton, GA",
                "sell_price": 1,
                "tags": None,
                "bogus": "ignored",
            }
        )
        assert offering.unit is Unit.EACH
        assert offering.status is OfferingStatus.DRAFT
        assert offering.created_at.tzinfo is not None
        assert offering.expires_at is None
        assert offering.tags == [] and offering.attachments == []


class TestBuyer:
    """Buyer validation, derived properties and serialisation."""

    def test_email_is_normalised_and_validated(self):
        assert Buyer(email="  Buyer@Example.COM ").email == "buyer@example.com"
        with pytest.raises(ValueError, match="Invalid buyer email"):
            Buyer(email="not-an-email")

    def test_status_string_is_coerced(self):
        assert Buyer(email="b@example.com", status="paused").status is BuyerStatus.PAUSED

    def test_first_name_and_destination(self):
        buyer = Buyer(email="b@example.com", name="  Marcus  Thibodeaux ", postal_code=" 73127 ", city_state="Oklahoma City, OK")
        assert buyer.first_name == "Marcus"
        assert buyer.destination == "73127"
        assert Buyer(email="b@example.com", city_state="Oklahoma City, OK").destination == "Oklahoma City, OK"
        anonymous = Buyer(email="b@example.com")
        assert anonymous.first_name == "" and anonymous.destination == ""

    def test_round_trip_and_defaults(self):
        buyer = Buyer(email="b@example.com", name="Priya", tags=["tile"], status=BuyerStatus.BOUNCED, created_at=NOW)
        data = buyer.to_dict()
        assert data["status"] == "bounced" and data["created_at"] == NOW.isoformat()
        assert Buyer.from_dict(data) == buyer
        minimal = Buyer.from_dict({"email": "c@example.com", "tags": None})
        assert minimal.status is BuyerStatus.ACTIVE and minimal.tags == [] and minimal.created_at.tzinfo is not None


class TestCampaign:
    """Campaign coercion and serialisation."""

    def test_kind_status_coerced_and_recipients_normalised(self):
        campaign = Campaign("c-1", "o-1", "blast", [" A@Example.com ", "", "  "], status="sent")
        assert campaign.kind is CampaignKind.BLAST
        assert campaign.status is CampaignStatus.SENT
        assert campaign.recipients == ["a@example.com"]

    def test_round_trip(self):
        campaign = Campaign(
            "c-1",
            "o-1",
            CampaignKind.PERSONAL,
            ["a@example.com"],
            subject="MARCUS>>$0.99/sf Planks",
            personal_note="Great deal.",
            status=CampaignStatus.SENT,
            created_at=NOW,
            sent_at=NOW + timedelta(minutes=5),
            thread_ids=["t-1"],
            error="",
        )
        data = campaign.to_dict()
        assert data["kind"] == "personal" and data["sent_at"] == (NOW + timedelta(minutes=5)).isoformat()
        assert Campaign.from_dict(data) == campaign

    def test_from_dict_defaults(self):
        campaign = Campaign.from_dict({"id": "c", "offering_id": "o", "kind": "internal", "recipients": None, "thread_ids": None})
        assert campaign.status is CampaignStatus.SCHEDULED
        assert campaign.recipients == [] and campaign.thread_ids == [] and campaign.sent_at is None


class TestContactAndActionRecord:
    """Enum coercion and e-mail normalisation on the audit records."""

    def test_contact(self):
        contact = Contact("o-1", " Buyer@Example.com ", "initial", NOW)
        assert contact.kind is ContactKind.INITIAL
        assert contact.buyer_email == "buyer@example.com"

    def test_action_record(self):
        action = ActionRecord("blast_sent", NOW, buyer_email=" Buyer@Example.com ")
        assert action.kind is ActionKind.BLAST_SENT
        assert action.buyer_email == "buyer@example.com"
        assert action.id is None


class TestMessages:
    """Inbound / outbound message normalisation."""

    def test_inbound_lower_cases_sender_and_header_names(self):
        inbound = InboundMessage("m", "t", " Buyer@Example.com ", "Re: deal", "body", NOW, headers={"X-Autoreply": "yes"})
        assert inbound.from_email == "buyer@example.com"
        assert inbound.headers == {"x-autoreply": "yes"}

    def test_outbound_normalises_recipients(self):
        message = OutboundMessage("Subject", "Body", to=[" A@X.com ", ""], cc=["B@X.com"], bcc=["c@x.com"])
        assert message.to == ["a@x.com"]
        assert message.all_recipients == ["a@x.com", "b@x.com", "c@x.com"]

    def test_outbound_requires_a_recipient_and_a_subject(self):
        with pytest.raises(ValueError, match="at least one recipient"):
            OutboundMessage("Subject", "Body")
        with pytest.raises(ValueError, match="subject"):
            OutboundMessage("   ", "Body", to=["a@x.com"])

    def test_send_result_defaults(self):
        result = SendResult("m-1", "t-1")
        assert result.draft_id == "" and result.dry_run is False


class TestClassification:
    """Intent coercion, confidence clamping and has_destination."""

    def test_intent_string_coerced_and_confidence_clamped(self):
        high = Classification("firm_offer", confidence=1.7)
        assert high.intent is ReplyIntent.FIRM_OFFER and high.confidence == 1.0
        low = Classification(ReplyIntent.UNKNOWN, confidence=-2)
        assert low.confidence == 0.0

    def test_invalid_intent_raises(self):
        with pytest.raises(ValueError):
            Classification("nope")

    def test_has_destination(self):
        assert Classification(ReplyIntent.DELIVERED_PRICE_REQUEST, postal_code="73127").has_destination
        assert Classification(ReplyIntent.DELIVERED_PRICE_REQUEST, destination="Oklahoma City, OK").has_destination
        assert not Classification(ReplyIntent.DELIVERED_PRICE_REQUEST).has_destination


class TestDeliveredQuote:
    """DeliveredQuote sanity checks."""

    @pytest.mark.parametrize(
        "overrides, match",
        [
            ({"distance_miles": -1.0}, "Distance"),
            ({"truckloads": 0.0}, "Truckloads"),
            ({"freight_per_truckload": -1.0}, "Freight"),
            ({"freight_total": -1.0}, "Freight"),
            ({"delivered_price_per_unit": 0.5}, "below FOB"),
        ],
    )
    def test_validation_errors(self, overrides, match):
        with pytest.raises(ValueError, match=match):
            make_quote(**overrides)

    def test_valid_quote(self):
        quote = make_quote(units_per_truckload=20000, units_total=20000, delivered_total=22600)
        assert quote.quoted_at.tzinfo is not None
        assert quote.delivered_total == 22600


class TestRunReport:
    """RunReport.summary."""

    def test_summary_dry_run_and_live(self):
        dry = RunReport(NOW).summary()
        assert dry.startswith("[DRY RUN] campaigns=0 sent=0 drafts=0") and dry.endswith("errors=0")
        live = RunReport(NOW, live=True, campaigns_sent=2, errors=["boom"]).summary()
        assert live.startswith("[LIVE] campaigns=2") and live.endswith("errors=1")
