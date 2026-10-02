"""Tests for the email_offerings.store module (SQLite persistence)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from email_offerings.models import (
    ActionKind,
    ActionRecord,
    Buyer,
    BuyerStatus,
    Campaign,
    CampaignKind,
    CampaignStatus,
    Contact,
    ContactKind,
    Offering,
    OfferingStatus,
    ReplyIntent,
    Unit,
)
from email_offerings.store import CSV_COLUMNS, MEMORY_PATH, Store, _LineTracker, _from_iso, _to_iso

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def _dt(**delta) -> datetime:
    """T0 shifted by a timedelta, e.g. ``_dt(days=-4)``."""
    return T0 + timedelta(**delta)


def make_offering(offering_id: str = "oak-spc-2026-10-01", **overrides) -> Offering:
    data = dict(
        id=offering_id,
        title="Silver Rustic Oak SPC Vinyl Click Flooring",
        description="6mm/20mil 7x48 SPC. DELETE RED internal DELETE RED",
        fob_location="Calhoun, GA",
        unit=Unit.SF,
        sell_price=0.99,
        quantity_available="approx 6 truckloads",
        units_per_truckload=28000.0,
        make_offers=True,
        status=OfferingStatus.DRAFT,
        created_at=T0,
        expires_at=_dt(days=30),
        cost_price=0.80,
        suggested_sell_note="Suggested Sell Below $1.19/sf",
        internal_notes="DELETE RED span text",
        tags=["spc", "flooring"],
        attachments=["/tmp/spec.pdf"],
    )
    data.update(overrides)
    return Offering(**data)


def make_buyer(email: str = "alice@example.com", **overrides) -> Buyer:
    data = dict(
        email=email,
        name="Alice Example",
        company="Example Flooring Outlet",
        postal_code="73127",
        city_state="Oklahoma City, OK",
        status=BuyerStatus.ACTIVE,
        tags=["dealer", "liquidator"],
        notes="prefers SPC",
        created_at=T0,
    )
    data.update(overrides)
    return Buyer(**data)


def make_campaign(campaign_id: str = "oak-spc-2026-10-01-blast-1", **overrides) -> Campaign:
    data = dict(
        id=campaign_id,
        offering_id="oak-spc-2026-10-01",
        kind=CampaignKind.BLAST,
        recipients=["Alice@example.com", "bob@example.com"],
        subject="$0.99/sf Silver Rustic Oak SPC (New 10/1)",
        personal_note="",
        status=CampaignStatus.SCHEDULED,
        created_at=T0,
        sent_at=None,
        thread_ids=[],
        error="",
    )
    data.update(overrides)
    return Campaign(**data)


def make_contact(
    buyer_email: str = "alice@example.com",
    kind: ContactKind = ContactKind.INITIAL,
    at: datetime = T0,
    offering_id: str = "oak-spc-2026-10-01",
    **overrides,
) -> Contact:
    data = dict(offering_id=offering_id, buyer_email=buyer_email, kind=kind, at=at)
    data.update(overrides)
    return Contact(**data)


@pytest.fixture
def store() -> Store:
    with Store() as s:
        yield s


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


class TestStoreLifecycle:
    """Construction, initialisation, file paths and closing."""

    def test_default_path_is_memory(self):
        s = Store()
        assert s.path == MEMORY_PATH
        assert s.get_offering("missing") is None
        s.close()

    def test_initialize_is_idempotent(self, store):
        store.upsert_buyer(make_buyer())
        store.initialize()
        store.initialize()
        assert store.get_buyer("alice@example.com") is not None

    def test_path_accepts_pathlib_and_persists(self, tmp_path):
        db_path: Path = tmp_path / "offerings.db"
        with Store(db_path) as s:
            s.upsert_offering(make_offering())
            s.set_state("last_inbox_poll", "2026-10-01")
        assert db_path.exists()
        with Store(str(db_path)) as reopened:
            assert reopened.get_offering("oak-spc-2026-10-01") is not None
            assert reopened.get_state("last_inbox_poll") == "2026-10-01"

    def test_missing_parent_directories_are_created(self, tmp_path):
        db_path = tmp_path / "nested" / "deeper" / "offerings.db"
        with Store(db_path):
            pass
        assert db_path.exists()

    def test_context_manager_closes_connection(self):
        with Store() as s:
            s.set_state("k", "v")
        with pytest.raises(sqlite3.ProgrammingError):
            s.get_state("k")

    def test_close_twice_is_harmless(self):
        s = Store()
        s.close()
        s.close()
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            _ = s.connection

    def test_all_tables_exist(self, store):
        names = {
            row["name"]
            for row in store.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        expected = {
            "offerings",
            "buyers",
            "campaigns",
            "thread_map",
            "contacts",
            "replies",
            "processed_messages",
            "actions",
            "state",
            "daily_sends",
        }
        assert expected <= names


# --------------------------------------------------------------------------- #
# Offerings
# --------------------------------------------------------------------------- #


class TestOfferings:
    """Round trips, listing and status changes for offerings."""

    def test_round_trip_preserves_every_field(self, store):
        offering = make_offering()
        store.upsert_offering(offering)
        loaded = store.get_offering(offering.id)
        assert loaded == offering
        assert loaded.unit is Unit.SF
        assert loaded.status is OfferingStatus.DRAFT
        assert loaded.make_offers is True
        assert loaded.tags == ["spc", "flooring"]
        assert loaded.attachments == ["/tmp/spec.pdf"]
        assert loaded.created_at.tzinfo is not None
        assert loaded.expires_at == _dt(days=30)

    def test_round_trip_with_optional_fields_empty(self, store):
        offering = make_offering(
            "pavers-tl",
            unit=Unit.TRUCKLOAD,
            sell_price=8500.0,
            units_per_truckload=None,
            make_offers=False,
            expires_at=None,
            cost_price=None,
            tags=[],
            attachments=[],
        )
        store.upsert_offering(offering)
        loaded = store.get_offering("pavers-tl")
        assert loaded == offering
        assert loaded.units_per_truckload is None
        assert loaded.expires_at is None
        assert loaded.cost_price is None
        assert loaded.make_offers is False

    def test_get_missing_returns_none(self, store):
        assert store.get_offering("nope") is None

    def test_upsert_overwrites_existing(self, store):
        store.upsert_offering(make_offering())
        store.upsert_offering(make_offering(sell_price=1.09, tags=["sale"], title="Updated title"))
        loaded = store.get_offering("oak-spc-2026-10-01")
        assert loaded.sell_price == 1.09
        assert loaded.tags == ["sale"]
        assert loaded.title == "Updated title"
        assert len(store.list_offerings()) == 1

    def test_list_all_ordered_by_created_at(self, store):
        store.upsert_offering(make_offering("newer", created_at=_dt(days=2)))
        store.upsert_offering(make_offering("older", created_at=_dt(days=-2)))
        store.upsert_offering(make_offering("middle", created_at=T0))
        assert [o.id for o in store.list_offerings()] == ["older", "middle", "newer"]

    def test_list_filtered_by_status(self, store):
        store.upsert_offering(make_offering("draft", status=OfferingStatus.DRAFT))
        store.upsert_offering(make_offering("active", status=OfferingStatus.ACTIVE))
        store.upsert_offering(make_offering("sold", status=OfferingStatus.SOLD))
        assert [o.id for o in store.list_offerings(OfferingStatus.ACTIVE)] == ["active"]
        assert store.list_offerings(OfferingStatus.EXPIRED) == []

    def test_set_offering_status(self, store):
        store.upsert_offering(make_offering())
        store.set_offering_status("oak-spc-2026-10-01", OfferingStatus.ACTIVE)
        assert store.get_offering("oak-spc-2026-10-01").status is OfferingStatus.ACTIVE
        assert store.get_offering("oak-spc-2026-10-01").is_active

    def test_set_status_unknown_offering_raises(self, store):
        with pytest.raises(KeyError, match="Unknown offering"):
            store.set_offering_status("ghost", OfferingStatus.ACTIVE)

    def test_non_utc_datetimes_are_normalised_to_utc(self, store):
        eastern = timezone(timedelta(hours=-4))
        local = datetime(2026, 10, 1, 8, 0, tzinfo=eastern)  # == 12:00 UTC
        store.upsert_offering(make_offering(created_at=local, expires_at=None))
        row = store.connection.execute("SELECT created_at FROM offerings").fetchone()
        assert row["created_at"] == "2026-10-01T12:00:00.000000+00:00"
        assert store.get_offering("oak-spc-2026-10-01").created_at == local

    def test_naive_datetimes_are_treated_as_utc(self, store):
        store.upsert_offering(make_offering(created_at=datetime(2026, 10, 1, 12, 0), expires_at=None))
        assert store.get_offering("oak-spc-2026-10-01").created_at == T0


# --------------------------------------------------------------------------- #
# Buyers
# --------------------------------------------------------------------------- #


class TestBuyers:
    """Round trips, case-insensitive lookup, listing and status changes."""

    def test_round_trip_preserves_every_field(self, store):
        buyer = make_buyer()
        store.upsert_buyer(buyer)
        loaded = store.get_buyer("alice@example.com")
        assert loaded == buyer
        assert loaded.status is BuyerStatus.ACTIVE
        assert loaded.tags == ["dealer", "liquidator"]
        assert loaded.created_at == T0

    def test_lookup_is_case_insensitive(self, store):
        store.upsert_buyer(make_buyer("Alice@Example.COM"))
        assert store.get_buyer("ALICE@example.com") is not None
        assert store.get_buyer("  alice@EXAMPLE.com ") .email == "alice@example.com"

    def test_get_missing_returns_none(self, store):
        assert store.get_buyer("nobody@example.com") is None

    def test_upsert_overwrites_existing(self, store):
        store.upsert_buyer(make_buyer())
        store.upsert_buyer(make_buyer(name="Alicia Example", tags=["restore"], postal_code=""))
        loaded = store.get_buyer("alice@example.com")
        assert loaded.name == "Alicia Example"
        assert loaded.tags == ["restore"]
        assert loaded.postal_code == ""
        assert len(store.list_buyers()) == 1

    def test_list_defaults_to_active_only(self, store):
        store.upsert_buyer(make_buyer("carol@example.com"))
        store.upsert_buyer(make_buyer("bob@example.com", status=BuyerStatus.UNSUBSCRIBED))
        store.upsert_buyer(make_buyer("alice@example.com"))
        assert [b.email for b in store.list_buyers()] == ["alice@example.com", "carol@example.com"]

    def test_list_with_status_none_returns_everyone(self, store):
        store.upsert_buyer(make_buyer("alice@example.com"))
        store.upsert_buyer(make_buyer("bob@example.com", status=BuyerStatus.BOUNCED))
        store.upsert_buyer(make_buyer("carol@example.com", status=BuyerStatus.PAUSED))
        assert len(store.list_buyers(status=None)) == 3
        assert [b.email for b in store.list_buyers(status=BuyerStatus.PAUSED)] == ["carol@example.com"]

    def test_list_filtered_by_tag(self, store):
        store.upsert_buyer(make_buyer("alice@example.com", tags=["dealer"]))
        store.upsert_buyer(make_buyer("bob@example.com", tags=["restore", "dealer"]))
        store.upsert_buyer(make_buyer("carol@example.com", tags=[]))
        assert [b.email for b in store.list_buyers(tag="dealer")] == ["alice@example.com", "bob@example.com"]
        assert [b.email for b in store.list_buyers(tag="restore")] == ["bob@example.com"]
        assert store.list_buyers(status=None, tag="nothing") == []

    def test_set_buyer_status_case_insensitive(self, store):
        store.upsert_buyer(make_buyer())
        store.set_buyer_status("ALICE@example.com", BuyerStatus.UNSUBSCRIBED)
        assert store.get_buyer("alice@example.com").status is BuyerStatus.UNSUBSCRIBED
        assert store.list_buyers() == []

    def test_set_status_unknown_buyer_raises(self, store):
        with pytest.raises(KeyError, match="Unknown buyer"):
            store.set_buyer_status("ghost@example.com", BuyerStatus.PAUSED)


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #


class TestCampaigns:
    """Add / update / get / list campaigns."""

    def test_round_trip_preserves_every_field(self, store):
        campaign = make_campaign(
            sent_at=_dt(hours=1),
            thread_ids=["thr-1", "thr-2"],
            status=CampaignStatus.SENT,
            error="",
            personal_note="Pretty great deal. -Dan",
        )
        store.add_campaign(campaign)
        loaded = store.get_campaign(campaign.id)
        assert loaded == campaign
        assert loaded.recipients == ["alice@example.com", "bob@example.com"]
        assert loaded.thread_ids == ["thr-1", "thr-2"]
        assert loaded.kind is CampaignKind.BLAST
        assert loaded.status is CampaignStatus.SENT
        assert loaded.sent_at == _dt(hours=1)

    def test_round_trip_without_sent_at(self, store):
        store.add_campaign(make_campaign())
        loaded = store.get_campaign("oak-spc-2026-10-01-blast-1")
        assert loaded.sent_at is None
        assert loaded.thread_ids == []

    def test_get_missing_returns_none(self, store):
        assert store.get_campaign("nope") is None

    def test_add_duplicate_raises_value_error(self, store):
        store.add_campaign(make_campaign())
        with pytest.raises(ValueError, match="already exists"):
            store.add_campaign(make_campaign())

    def test_update_replaces_fields(self, store):
        campaign = make_campaign()
        store.add_campaign(campaign)
        campaign.status = CampaignStatus.SENT
        campaign.sent_at = _dt(minutes=5)
        campaign.thread_ids = ["thr-9"]
        campaign.error = ""
        store.update_campaign(campaign)
        loaded = store.get_campaign(campaign.id)
        assert loaded.status is CampaignStatus.SENT
        assert loaded.sent_at == _dt(minutes=5)
        assert loaded.thread_ids == ["thr-9"]

    def test_update_unknown_campaign_raises(self, store):
        with pytest.raises(KeyError, match="Unknown campaign"):
            store.update_campaign(make_campaign("never-added"))

    def test_list_filters_and_ordering(self, store):
        store.add_campaign(make_campaign("c2", offering_id="off-a", created_at=_dt(hours=2)))
        store.add_campaign(make_campaign("c1", offering_id="off-a", created_at=_dt(hours=1)))
        store.add_campaign(
            make_campaign("c3", offering_id="off-b", created_at=_dt(hours=3), status=CampaignStatus.SENT)
        )
        assert [c.id for c in store.list_campaigns()] == ["c1", "c2", "c3"]
        assert [c.id for c in store.list_campaigns(offering_id="off-a")] == ["c1", "c2"]
        assert [c.id for c in store.list_campaigns(status=CampaignStatus.SENT)] == ["c3"]
        assert [c.id for c in store.list_campaigns(offering_id="off-b", status=CampaignStatus.SENT)] == ["c3"]
        assert store.list_campaigns(offering_id="off-b", status=CampaignStatus.SCHEDULED) == []


# --------------------------------------------------------------------------- #
# Thread map
# --------------------------------------------------------------------------- #


class TestThreadMap:
    """Thread → offering / buyer mapping."""

    def test_map_and_lookup(self, store):
        store.map_thread("thr-1", "off-a", "Alice@Example.com")
        assert store.offering_for_thread("thr-1") == "off-a"
        assert store.buyer_for_thread("thr-1") == "alice@example.com"

    def test_unknown_thread_returns_none(self, store):
        assert store.offering_for_thread("ghost") is None
        assert store.buyer_for_thread("ghost") is None

    def test_blank_buyer_reads_as_none(self, store):
        store.map_thread("thr-1", "off-a")
        assert store.offering_for_thread("thr-1") == "off-a"
        assert store.buyer_for_thread("thr-1") is None

    def test_remap_updates_offering_and_buyer(self, store):
        store.map_thread("thr-1", "off-a", "alice@example.com")
        store.map_thread("thr-1", "off-b", "bob@example.com")
        assert store.offering_for_thread("thr-1") == "off-b"
        assert store.buyer_for_thread("thr-1") == "bob@example.com"
        count = store.connection.execute("SELECT COUNT(*) AS n FROM thread_map").fetchone()["n"]
        assert count == 1

    def test_remap_with_blank_buyer_keeps_known_buyer(self, store):
        store.map_thread("thr-1", "off-a", "alice@example.com")
        store.map_thread("thr-1", "off-b")
        assert store.offering_for_thread("thr-1") == "off-b"
        assert store.buyer_for_thread("thr-1") == "alice@example.com"


# --------------------------------------------------------------------------- #
# Contacts and replies
# --------------------------------------------------------------------------- #


class TestContactsAndReplies:
    """Recording touches and replies."""

    def test_record_and_list_contacts_in_time_order(self, store):
        store.record_contact(make_contact(at=_dt(hours=2), kind=ContactKind.FOLLOW_UP, thread_id="t2"))
        store.record_contact(
            make_contact(at=T0, thread_id="t1", message_id="m1", campaign_id="c1")
        )
        contacts = store.contacts("oak-spc-2026-10-01")
        assert [c.kind for c in contacts] == [ContactKind.INITIAL, ContactKind.FOLLOW_UP]
        first = contacts[0]
        assert first.buyer_email == "alice@example.com"
        assert first.at == T0
        assert first.thread_id == "t1"
        assert first.message_id == "m1"
        assert first.campaign_id == "c1"

    def test_contacts_filter_by_buyer_and_kind(self, store):
        store.record_contact(make_contact("alice@example.com"))
        store.record_contact(make_contact("bob@example.com"))
        store.record_contact(make_contact("bob@example.com", kind=ContactKind.QUOTE, at=_dt(hours=1)))
        store.record_contact(make_contact("bob@example.com", offering_id="other"))
        assert len(store.contacts("oak-spc-2026-10-01")) == 3
        assert len(store.contacts("oak-spc-2026-10-01", buyer_email="BOB@example.com")) == 2
        assert len(store.contacts("oak-spc-2026-10-01", kind=ContactKind.INITIAL)) == 2
        only = store.contacts("oak-spc-2026-10-01", buyer_email="bob@example.com", kind=ContactKind.QUOTE)
        assert len(only) == 1 and only[0].kind is ContactKind.QUOTE
        assert store.contacts("missing") == []

    def test_record_reply_and_has_replied(self, store):
        assert store.has_replied("off-a", "alice@example.com") is False
        store.record_reply("off-a", "Alice@Example.com", "msg-1", ReplyIntent.INTERESTED, T0)
        assert store.has_replied("off-a", "alice@example.com") is True
        assert store.has_replied("off-a", "ALICE@EXAMPLE.COM") is True
        assert store.has_replied("off-b", "alice@example.com") is False
        assert store.has_replied("off-a", "bob@example.com") is False

    def test_reply_row_is_stored_with_intent_and_time(self, store):
        store.record_reply("off-a", "alice@example.com", "msg-1", ReplyIntent.FIRM_OFFER, T0)
        row = store.connection.execute("SELECT * FROM replies").fetchone()
        assert row["intent"] == "firm_offer"
        assert row["message_id"] == "msg-1"
        assert row["at"] == "2026-10-01T12:00:00.000000+00:00"


# --------------------------------------------------------------------------- #
# Follow-up selection
# --------------------------------------------------------------------------- #


class TestBuyersDueFollowUp:
    """The follow-up query honours every contract condition."""

    OFFERING = "oak-spc-2026-10-01"
    CUTOFF = _dt(days=-3)

    def _seed(self, store, email: str, *, status: BuyerStatus = BuyerStatus.ACTIVE, initial_at=None):
        store.upsert_buyer(make_buyer(email, status=status))
        store.record_contact(make_contact(email, at=initial_at or _dt(days=-5)))

    def test_buyer_contacted_before_cutoff_is_due(self, store):
        self._seed(store, "alice@example.com")
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == ["alice@example.com"]

    def test_buyer_contacted_after_cutoff_is_not_due(self, store):
        self._seed(store, "alice@example.com", initial_at=_dt(days=-1))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []

    def test_contact_exactly_at_cutoff_is_not_due(self, store):
        self._seed(store, "alice@example.com", initial_at=self.CUTOFF)
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []

    def test_replied_buyer_is_excluded(self, store):
        self._seed(store, "alice@example.com")
        self._seed(store, "bob@example.com")
        store.record_reply(self.OFFERING, "alice@example.com", "m1", ReplyIntent.QUESTION, _dt(days=-4))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == ["bob@example.com"]

    def test_reply_about_other_offering_does_not_exclude(self, store):
        self._seed(store, "alice@example.com")
        store.record_reply("other-offering", "alice@example.com", "m1", ReplyIntent.INTERESTED, T0)
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == ["alice@example.com"]

    @pytest.mark.parametrize("status", [BuyerStatus.UNSUBSCRIBED, BuyerStatus.BOUNCED, BuyerStatus.PAUSED])
    def test_non_active_buyers_are_excluded(self, store, status):
        self._seed(store, "alice@example.com", status=status)
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []

    def test_buyer_unsubscribed_after_contact_is_excluded(self, store):
        self._seed(store, "alice@example.com")
        store.set_buyer_status("alice@example.com", BuyerStatus.UNSUBSCRIBED)
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []

    def test_contact_without_buyer_record_is_excluded(self, store):
        store.record_contact(make_contact("stranger@example.com", at=_dt(days=-5)))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []

    def test_follow_up_count_cap(self, store):
        self._seed(store, "alice@example.com")
        store.record_contact(make_contact("alice@example.com", kind=ContactKind.FOLLOW_UP, at=_dt(days=-2)))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 2) == ["alice@example.com"]
        store.record_contact(make_contact("alice@example.com", kind=ContactKind.FOLLOW_UP, at=_dt(days=-1)))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 2) == []

    def test_max_follow_ups_zero_means_nobody(self, store):
        self._seed(store, "alice@example.com")
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 0) == []

    def test_other_contact_kinds_do_not_count_as_follow_ups(self, store):
        self._seed(store, "alice@example.com")
        store.record_contact(make_contact("alice@example.com", kind=ContactKind.QUOTE, at=_dt(days=-2)))
        store.record_contact(make_contact("alice@example.com", kind=ContactKind.DRAFT, at=_dt(days=-2)))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == ["alice@example.com"]

    def test_only_initial_contacts_qualify(self, store):
        store.upsert_buyer(make_buyer("alice@example.com"))
        store.record_contact(make_contact("alice@example.com", kind=ContactKind.QUOTE, at=_dt(days=-5)))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []

    def test_other_offering_contacts_are_ignored(self, store):
        store.upsert_buyer(make_buyer("alice@example.com"))
        store.record_contact(make_contact("alice@example.com", at=_dt(days=-5), offering_id="other"))
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == []

    def test_results_are_distinct_and_sorted(self, store):
        self._seed(store, "carol@example.com")
        self._seed(store, "alice@example.com")
        self._seed(store, "bob@example.com")
        store.record_contact(make_contact("alice@example.com", at=_dt(days=-6)))  # second INITIAL
        assert store.buyers_due_follow_up(self.OFFERING, self.CUTOFF, 1) == [
            "alice@example.com",
            "bob@example.com",
            "carol@example.com",
        ]


# --------------------------------------------------------------------------- #
# Inbox idempotency
# --------------------------------------------------------------------------- #


class TestProcessedMessages:
    """Each inbound message id is handled at most once."""

    def test_mark_and_check(self, store):
        assert store.is_processed("msg-1") is False
        store.mark_processed("msg-1", T0)
        assert store.is_processed("msg-1") is True
        assert store.is_processed("msg-2") is False

    def test_mark_twice_is_idempotent_and_keeps_first_time(self, store):
        store.mark_processed("msg-1", T0)
        store.mark_processed("msg-1", _dt(hours=5))
        rows = store.connection.execute("SELECT * FROM processed_messages").fetchall()
        assert len(rows) == 1
        assert rows[0]["at"] == "2026-10-01T12:00:00.000000+00:00"

    def test_survives_reopen(self, tmp_path):
        db = tmp_path / "idem.db"
        with Store(db) as s:
            s.mark_processed("msg-1", T0)
        with Store(db) as s:
            assert s.is_processed("msg-1") is True


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #


class TestActions:
    """Audit log append and queries."""

    def _add(self, store, kind: ActionKind, at: datetime, **fields) -> ActionRecord:
        return store.add_action(ActionRecord(kind=kind, at=at, **fields))

    def test_add_returns_record_with_id(self, store):
        record = ActionRecord(
            kind=ActionKind.QUOTE_SENT,
            at=T0,
            offering_id="off-a",
            buyer_email="Alice@Example.com",
            thread_id="thr-1",
            message_id="msg-1",
            details="freight $2900",
        )
        assert record.id is None
        returned = store.add_action(record)
        assert returned is record
        assert returned.id == 1
        second = self._add(store, ActionKind.IGNORED, _dt(minutes=1))
        assert second.id == 2

    def test_round_trip_preserves_every_field(self, store):
        self._add(
            store,
            ActionKind.OFFER_ESCALATED,
            T0,
            offering_id="off-a",
            buyer_email="alice@example.com",
            thread_id="thr-1",
            message_id="msg-1",
            details="offer $0.85/sf",
        )
        [loaded] = store.list_actions()
        assert loaded.kind is ActionKind.OFFER_ESCALATED
        assert loaded.at == T0
        assert loaded.offering_id == "off-a"
        assert loaded.buyer_email == "alice@example.com"
        assert loaded.thread_id == "thr-1"
        assert loaded.message_id == "msg-1"
        assert loaded.details == "offer $0.85/sf"
        assert loaded.id == 1

    def test_list_is_ascending_by_time(self, store):
        self._add(store, ActionKind.IGNORED, _dt(hours=2), details="third")
        self._add(store, ActionKind.IGNORED, _dt(hours=0), details="first")
        self._add(store, ActionKind.IGNORED, _dt(hours=1), details="second")
        assert [a.details for a in store.list_actions()] == ["first", "second", "third"]

    def test_list_empty(self, store):
        assert store.list_actions() == []
        assert store.list_actions(limit=5) == []

    def test_since_is_inclusive(self, store):
        self._add(store, ActionKind.IGNORED, _dt(hours=-1), details="old")
        self._add(store, ActionKind.IGNORED, T0, details="edge")
        self._add(store, ActionKind.IGNORED, _dt(hours=1), details="new")
        assert [a.details for a in store.list_actions(since=T0)] == ["edge", "new"]

    def test_filter_by_kind(self, store):
        self._add(store, ActionKind.BLAST_SENT, T0)
        self._add(store, ActionKind.ERROR, _dt(minutes=1))
        self._add(store, ActionKind.BLAST_SENT, _dt(minutes=2))
        blasts = store.list_actions(kind=ActionKind.BLAST_SENT)
        assert len(blasts) == 2 and all(a.kind is ActionKind.BLAST_SENT for a in blasts)
        assert [a.kind for a in store.list_actions(since=_dt(minutes=1), kind=ActionKind.ERROR)] == [
            ActionKind.ERROR
        ]

    def test_limit_returns_most_recent_in_ascending_order(self, store):
        for i in range(5):
            self._add(store, ActionKind.IGNORED, _dt(minutes=i), details=f"a{i}")
        assert [a.details for a in store.list_actions(limit=2)] == ["a3", "a4"]
        assert [a.details for a in store.list_actions(limit=10)] == [f"a{i}" for i in range(5)]

    def test_limit_combined_with_filters(self, store):
        for i in range(4):
            self._add(store, ActionKind.FOLLOW_UP_SENT, _dt(minutes=i), details=f"f{i}")
        self._add(store, ActionKind.ERROR, _dt(minutes=9), details="e")
        result = store.list_actions(since=_dt(minutes=1), kind=ActionKind.FOLLOW_UP_SENT, limit=2)
        assert [a.details for a in result] == ["f2", "f3"]

    def test_same_timestamp_preserves_insertion_order(self, store):
        self._add(store, ActionKind.IGNORED, T0, details="x")
        self._add(store, ActionKind.IGNORED, T0, details="y")
        assert [a.details for a in store.list_actions()] == ["x", "y"]
        assert [a.details for a in store.list_actions(limit=1)] == ["y"]


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


class TestState:
    """Key/value state."""

    def test_missing_key_returns_default(self, store):
        assert store.get_state("last_inbox_poll") is None
        assert store.get_state("last_inbox_poll", "never") == "never"

    def test_set_get_and_overwrite(self, store):
        store.set_state("last_inbox_poll", "2026-10-01T00:00:00+00:00")
        assert store.get_state("last_inbox_poll") == "2026-10-01T00:00:00+00:00"
        store.set_state("last_inbox_poll", "2026-10-02T00:00:00+00:00")
        assert store.get_state("last_inbox_poll") == "2026-10-02T00:00:00+00:00"
        assert store.connection.execute("SELECT COUNT(*) AS n FROM state").fetchone()["n"] == 1


# --------------------------------------------------------------------------- #
# Daily send counters
# --------------------------------------------------------------------------- #


class TestDailySends:
    """Per-day send counters."""

    def test_unknown_day_is_zero(self, store):
        assert store.sends_on(date(2026, 10, 1)) == 0

    def test_add_accumulates(self, store):
        store.add_sends(date(2026, 10, 1), 50)
        store.add_sends(date(2026, 10, 1), 25)
        assert store.sends_on(date(2026, 10, 1)) == 75

    def test_days_are_independent(self, store):
        store.add_sends(date(2026, 10, 1), 10)
        store.add_sends(date(2026, 10, 2), 3)
        assert store.sends_on(date(2026, 10, 1)) == 10
        assert store.sends_on(date(2026, 10, 2)) == 3
        assert store.sends_on(date(2026, 10, 3)) == 0

    def test_datetime_is_keyed_by_its_date(self, store):
        store.add_sends(datetime(2026, 10, 1, 23, 59, tzinfo=timezone.utc), 4)
        assert store.sends_on(date(2026, 10, 1)) == 4
        assert store.sends_on(datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)) == 4
        row = store.connection.execute("SELECT day FROM daily_sends").fetchone()
        assert row["day"] == "2026-10-01"


# --------------------------------------------------------------------------- #
# CSV import
# --------------------------------------------------------------------------- #


class TestImportBuyersCsv:
    """Buyer list import from CSV."""

    HEADER = ",".join(CSV_COLUMNS)

    def _write(self, tmp_path: Path, text: str, name: str = "buyers.csv") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_basic_import_with_tags(self, store, tmp_path):
        path = self._write(
            tmp_path,
            f"{self.HEADER}\n"
            "alice@example.com,Alice Example,Example Flooring,73127,\"Oklahoma City, OK\",dealer|liquidator\n"
            "Bob@Example.com,Bob Example,,,,\n",
        )
        imported, rejected = store.import_buyers_csv(path)
        assert imported == 2
        assert rejected == []
        alice = store.get_buyer("alice@example.com")
        assert alice.name == "Alice Example"
        assert alice.company == "Example Flooring"
        assert alice.postal_code == "73127"
        assert alice.city_state == "Oklahoma City, OK"
        assert alice.tags == ["dealer", "liquidator"]
        assert alice.status is BuyerStatus.ACTIVE
        bob = store.get_buyer("bob@example.com")
        assert bob.name == "Bob Example"
        assert bob.tags == []

    def test_path_accepts_str(self, store, tmp_path):
        path = self._write(tmp_path, f"{self.HEADER}\ncarol@example.com,Carol,,,,\n")
        assert store.import_buyers_csv(str(path)) == (1, [])

    def test_invalid_emails_are_rejected_with_line_text(self, store, tmp_path):
        path = self._write(
            tmp_path,
            f"{self.HEADER}\n"
            "alice@example.com,Alice,,,,\n"
            "not-an-email,Nobody,Acme,,,\n"
            ",Blank Email,,,,\n"
            "bob@example.com,Bob,,,,\n",
        )
        imported, rejected = store.import_buyers_csv(path)
        assert imported == 2
        assert rejected == ["not-an-email,Nobody,Acme,,,", ",Blank Email,,,,"]
        assert store.get_buyer("alice@example.com") is not None
        assert store.get_buyer("bob@example.com") is not None
        assert len(store.list_buyers(status=None)) == 2

    def test_blank_lines_and_short_rows_are_tolerated(self, store, tmp_path):
        path = self._write(
            tmp_path,
            f"\n{self.HEADER}\n\nalice@example.com,Alice\n   \n,,,\nbob@example.com\n",
        )
        imported, rejected = store.import_buyers_csv(path)
        assert imported == 2
        assert rejected == []
        assert store.get_buyer("alice@example.com").name == "Alice"
        assert store.get_buyer("bob@example.com").name == ""

    def test_header_is_case_insensitive_and_order_free(self, store, tmp_path):
        path = self._write(
            tmp_path,
            "Name, EMAIL ,Tags,extra_column\nAlice Example,alice@example.com,restore|dealer,ignored\n",
        )
        assert store.import_buyers_csv(path) == (1, [])
        alice = store.get_buyer("alice@example.com")
        assert alice.name == "Alice Example"
        assert alice.tags == ["restore", "dealer"]
        assert alice.company == ""

    def test_utf8_bom_is_ignored(self, store, tmp_path):
        path = tmp_path / "bom.csv"
        path.write_bytes(b"\xef\xbb\xbf" + f"{self.HEADER}\nalice@example.com,Alice,,,,\n".encode("utf-8"))
        assert store.import_buyers_csv(path) == (1, [])

    def test_missing_email_column_raises(self, store, tmp_path):
        path = self._write(tmp_path, "name,company\nAlice,Acme\n")
        with pytest.raises(ValueError, match="header row required"):
            store.import_buyers_csv(path)

    def test_data_without_header_raises(self, store, tmp_path):
        path = self._write(tmp_path, "alice@example.com,Alice,,,,\n")
        with pytest.raises(ValueError, match="header row required"):
            store.import_buyers_csv(path)
        assert store.get_buyer("alice@example.com") is None

    def test_empty_file_raises(self, store, tmp_path):
        path = self._write(tmp_path, "")
        with pytest.raises(ValueError, match="empty"):
            store.import_buyers_csv(path)

    def test_blank_fields_do_not_overwrite_existing_values(self, store, tmp_path):
        store.upsert_buyer(
            make_buyer(
                "alice@example.com",
                name="Alice Example",
                company="Example Flooring",
                postal_code="73127",
                city_state="Oklahoma City, OK",
                tags=["dealer"],
                notes="keep me",
                status=BuyerStatus.PAUSED,
                created_at=_dt(days=-100),
            )
        )
        path = self._write(tmp_path, f"{self.HEADER}\nALICE@example.com,,,,,\n")
        assert store.import_buyers_csv(path) == (1, [])
        alice = store.get_buyer("alice@example.com")
        assert alice.name == "Alice Example"
        assert alice.company == "Example Flooring"
        assert alice.postal_code == "73127"
        assert alice.city_state == "Oklahoma City, OK"
        assert alice.tags == ["dealer"]
        assert alice.notes == "keep me"
        assert alice.status is BuyerStatus.PAUSED
        assert alice.created_at == _dt(days=-100)
        assert len(store.list_buyers(status=None)) == 1

    def test_non_blank_fields_update_existing_and_tags_merge(self, store, tmp_path):
        store.upsert_buyer(
            make_buyer("alice@example.com", name="Old Name", postal_code="30701", tags=["dealer"], status=BuyerStatus.UNSUBSCRIBED)
        )
        path = self._write(
            tmp_path,
            f"{self.HEADER}\nalice@example.com,New Name,New Co,73127,\"Oklahoma City, OK\",restore|dealer\n",
        )
        assert store.import_buyers_csv(path) == (1, [])
        alice = store.get_buyer("alice@example.com")
        assert alice.name == "New Name"
        assert alice.company == "New Co"
        assert alice.postal_code == "73127"
        assert alice.city_state == "Oklahoma City, OK"
        assert alice.tags == ["dealer", "restore"]
        assert alice.status is BuyerStatus.UNSUBSCRIBED  # status preserved

    def test_quoted_field_spanning_lines_is_reported_verbatim_when_rejected(self, store, tmp_path):
        path = self._write(
            tmp_path,
            f'{self.HEADER}\nbad email,"Multi\nLine Name",,,,\n',
        )
        imported, rejected = store.import_buyers_csv(path)
        assert imported == 0
        assert rejected == ['bad email,"Multi\nLine Name",,,,']

    def test_tags_are_stripped_and_empties_dropped(self, store, tmp_path):
        path = self._write(tmp_path, f"{self.HEADER}\nalice@example.com,,,,, dealer | |restore \n")
        store.import_buyers_csv(path)
        assert store.get_buyer("alice@example.com").tags == ["dealer", "restore"]


class TestTimestampHelpers:
    """ISO-8601 serialization used for every stored datetime."""

    def test_to_iso_normalises_to_utc_with_fixed_precision(self):
        assert _to_iso(None) is None
        assert _to_iso(T0) == "2026-10-01T12:00:00.000000+00:00"
        assert _to_iso(datetime(2026, 10, 1, 12, 0)) == "2026-10-01T12:00:00.000000+00:00"
        eastern = timezone(timedelta(hours=-4))
        assert _to_iso(datetime(2026, 10, 1, 8, 0, tzinfo=eastern)) == "2026-10-01T12:00:00.000000+00:00"

    def test_from_iso_handles_empty_naive_and_aware(self):
        assert _from_iso(None) is None
        assert _from_iso("") is None
        assert _from_iso("2026-10-01T12:00:00.000000+00:00") == T0
        assert _from_iso("2026-10-01T12:00:00") == T0

    def test_round_trip_is_lossless(self):
        when = datetime(2026, 10, 1, 12, 34, 56, 789012, tzinfo=timezone.utc)
        assert _from_iso(_to_iso(when)) == when


class TestLineTracker:
    """The helper that lets the CSV import report raw rejected lines."""

    def test_take_returns_lines_consumed_since_last_take(self):
        tracker = _LineTracker(["a,b\r\n", "c,d\n", "e,f"])
        assert next(tracker) == "a,b\r\n"
        assert tracker.take() == "a,b"
        assert next(tracker) == "c,d\n"
        assert next(tracker) == "e,f"
        assert tracker.take() == "c,d\ne,f"
        assert tracker.take() == ""
        with pytest.raises(StopIteration):
            next(tracker)
        assert iter(tracker) is tracker
