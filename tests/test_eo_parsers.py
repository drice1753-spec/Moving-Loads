"""Tests for email_offerings.parsers (cost sheets, offering files, buyer lines)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from email_offerings.models import Buyer, Offering, OfferingStatus, Unit, make_offering_id
from email_offerings.parsers import (
    load_offering_file,
    parse_buyer_line,
    parse_internal_sheet,
    parse_price_unit,
)

NOW = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)

# Real-shaped internal cost sheet (invented product, no real people).
SHEET_SUBJECT = "$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (AWR 10/1)"
SHEET_TITLE = "Silver Rustic Oak SPC Vinyl Click Flooring"
SHEET_BODY = (
    "DELETE RED...FOB: Calhoun, GA...COST FOB: $0.99/sf...Suggested Sell Below..."
    "BRING BACK ALL FIRM OFFERS...DELETE RED. New deal on some nice silver rustic oak spc "
    "vinyl click flooring. All first quality product with an attached pad. "
    "Approx 6 truckloads available."
)
INTERNAL_MARKERS = ("DELETE RED", "COST FOB", "Suggested Sell", "BRING BACK")


# --------------------------------------------------------------------------- #
# parse_price_unit
# --------------------------------------------------------------------------- #


class TestParsePriceUnit:
    """Tests for parse_price_unit."""

    @pytest.mark.parametrize(
        "text, price, unit",
        [
            ("$0.99/sf", 0.99, Unit.SF),
            ("$0.60/SF", 0.60, Unit.SF),
            ("$8500/TL", 8500.0, Unit.TRUCKLOAD),
            ("$8,500/TL", 8500.0, Unit.TRUCKLOAD),
            ("$8,500 per truckload", 8500.0, Unit.TRUCKLOAD),
            ("$11.90/sy", 11.90, Unit.SY),
            ("0.60/sf", 0.60, Unit.SF),
            ("99 cents/sf", 0.99, Unit.SF),
            ("99¢/sf", 0.99, Unit.SF),
            ("5 cents a sf", 0.05, Unit.SF),
            ("$45 each", 45.0, Unit.EACH),
            ("$45/ea.", 45.0, Unit.EACH),
            ("$2/pcs", 2.0, Unit.EACH),
            ("$1.25 per sq. ft.", 1.25, Unit.SF),
            ("$1.25 per square foot", 1.25, Unit.SF),
            ("$3 / lin ft", 3.0, Unit.LF),
            ("$450 per pallet", 450.0, Unit.PALLET),
            ("$450/plt", 450.0, Unit.PALLET),
            ("USD 8,500 / truck", 8500.0, Unit.TRUCKLOAD),
            ("  $0.99/sf  ", 0.99, Unit.SF),
            ("$0.99/sf.", 0.99, Unit.SF),
            (".75/sf", 0.75, Unit.SF),
            ("$1,234,567/TL", 1234567.0, Unit.TRUCKLOAD),
        ],
    )
    def test_parses_common_forms(self, text, price, unit):
        assert parse_price_unit(text) == (pytest.approx(price), unit)

    def test_text_after_unit_is_ignored(self):
        assert parse_price_unit("$0.99/sf Silver Rustic Oak SPC") == (0.99, Unit.SF)
        assert parse_price_unit("$8,500 per truckload delivered") == (8500.0, Unit.TRUCKLOAD)

    @pytest.mark.parametrize(
        "text",
        [
            "hello", "", "   ", "$", "free", "10/1", "$0.99", "$0.99/xyz", "$0.99//", "$0.99 / ...",
            "5mm/12mil", "7x48 SPC", "-$5/sf", "6 truckloads Oak",
        ],
    )
    def test_nonsense_raises(self, text):
        with pytest.raises(ValueError):
            parse_price_unit(text)

    def test_none_raises(self):
        with pytest.raises(ValueError, match="price"):
            parse_price_unit(None)  # type: ignore[arg-type]

    def test_missing_unit_message_is_clear(self):
        with pytest.raises(ValueError, match="unit"):
            parse_price_unit("$0.99")


# --------------------------------------------------------------------------- #
# parse_internal_sheet
# --------------------------------------------------------------------------- #


class TestParseInternalSheet:
    """Tests for parse_internal_sheet with the AWR cost-sheet fixture."""

    def test_fixture_parses_completely(self):
        offering = parse_internal_sheet(SHEET_SUBJECT, SHEET_BODY, now=NOW)

        assert isinstance(offering, Offering)
        assert offering.title == SHEET_TITLE
        assert offering.id == make_offering_id(SHEET_TITLE, NOW)
        assert offering.status == OfferingStatus.DRAFT
        assert offering.created_at == NOW
        assert offering.fob_location == "Calhoun, GA"
        assert offering.unit == Unit.SF
        assert offering.cost_price == 0.99
        assert offering.sell_price == 0.99  # falls back to cost when no SELL marker
        assert offering.make_offers is True
        assert offering.suggested_sell_note
        assert "Suggested Sell" in offering.suggested_sell_note
        assert "6 truckloads" in offering.quantity_available
        assert offering.expires_at is None
        assert offering.units_per_truckload is None

    def test_title_has_no_price_prefix_or_date_suffix(self):
        offering = parse_internal_sheet(SHEET_SUBJECT, SHEET_BODY, now=NOW)

        assert not offering.title.startswith("$")
        assert "AWR" not in offering.title
        assert "10/1" not in offering.title

    def test_description_is_customer_safe(self):
        offering = parse_internal_sheet(SHEET_SUBJECT, SHEET_BODY, now=NOW)

        for marker in INTERNAL_MARKERS:
            assert marker.lower() not in offering.description.lower()
        assert offering.description.startswith("New deal on some nice silver rustic oak")
        assert "attached pad" in offering.description
        assert "Approx 6 truckloads available." in offering.description

    def test_internal_notes_hold_removed_span(self):
        offering = parse_internal_sheet(SHEET_SUBJECT, SHEET_BODY, now=NOW)

        assert "COST FOB: $0.99/sf" in offering.internal_notes
        assert "Suggested Sell Below" in offering.internal_notes
        assert "BRING BACK ALL FIRM OFFERS" in offering.internal_notes
        assert "New deal" not in offering.internal_notes

    def test_sell_marker_sets_sell_price(self):
        body = SHEET_BODY.replace("Suggested Sell Below", "SELL: $1.19/sf")

        offering = parse_internal_sheet(SHEET_SUBJECT, body, now=NOW)

        assert offering.cost_price == 0.99
        assert offering.sell_price == 1.19
        assert offering.unit == Unit.SF

    def test_suggested_sell_with_price_sets_sell_price_and_note(self):
        body = SHEET_BODY.replace("Suggested Sell Below", "Suggested Sell: $1.19/sf")

        offering = parse_internal_sheet(SHEET_SUBJECT, body, now=NOW)

        assert offering.sell_price == 1.19
        assert offering.cost_price == 0.99
        assert "Suggested Sell: $1.19/sf" in offering.suggested_sell_note
        assert "1.19" not in offering.description or "Suggested Sell" not in offering.description

    def test_make_offers_subject_form(self):
        subject = "MAKE OFFERS: 5mm/12mil Silver Rustic Oak SPC Vinyl Click (New 10/1)"

        offering = parse_internal_sheet(subject, SHEET_BODY, now=NOW)

        assert offering.title == "5mm/12mil Silver Rustic Oak SPC Vinyl Click"
        assert offering.make_offers is True
        assert offering.cost_price == 0.99
        assert offering.sell_price == 0.99
        assert offering.unit == Unit.SF
        assert offering.id == make_offering_id("5mm/12mil Silver Rustic Oak SPC Vinyl Click", NOW)

    def test_make_offers_subject_without_body_marker(self):
        body = "FOB: Dalton, GA\nCOST FOB: $0.60/sf\nNice carpet tile, 24 pallets available."

        offering = parse_internal_sheet("MAKE OFFERS: Carpet Tile (New 10/1)", body, now=NOW)

        assert offering.make_offers is True
        assert offering.title == "Carpet Tile"

    def test_make_offers_false_without_any_marker(self):
        body = "FOB: Dalton, GA\nCOST FOB: $0.60/sf\nNice carpet tile."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.make_offers is False

    def test_make_offers_body_marker_variants(self):
        for marker in ("BRING BACK ALL OFFERS", "bring back firm offers", "MAKE OFFERS"):
            body = f"FOB: Dalton, GA\nCOST FOB: $0.60/sf\n{marker}\nNice carpet tile."
            assert parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW).make_offers is True

    def test_newline_separated_body(self):
        body = (
            "FOB: Dalton, GA\n"
            "COST FOB: $0.89/sf\n"
            "SELL: $1.19/sf\n"
            "Approx 24,000 sf per truckload. 2 TL available.\n"
            "Nice carpet tile, first quality."
        )

        offering = parse_internal_sheet("$0.89/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.fob_location == "Dalton, GA"
        assert offering.cost_price == 0.89
        assert offering.sell_price == 1.19
        assert offering.units_per_truckload == 24000.0
        assert offering.quantity_available == "2 TL available"
        assert "COST FOB" not in offering.description
        assert "SELL:" not in offering.description
        assert "FOB: Dalton" not in offering.description
        assert "Nice carpet tile, first quality." in offering.description
        assert "COST FOB: $0.89/sf" in offering.internal_notes
        assert "SELL: $1.19/sf" in offering.internal_notes

    def test_reply_prefix_and_update_suffix_are_stripped(self):
        subject = "Re: Fwd: $0.89/sf Carpet Tile (AWR 10/1) - 10/1 update"

        offering = parse_internal_sheet(subject, "FOB: Dalton, GA\nCOST FOB: $0.89/sf", now=NOW)

        assert offering.title == "Carpet Tile"

    def test_personal_forward_prefix_is_stripped(self):
        subject = "JORDAN>>$0.99/sf 6mm/20mil 7x48 SPC Vinyl Click (New 10/1)"

        offering = parse_internal_sheet(subject, "FOB: Calhoun, GA\nGreat deal.", now=NOW)

        assert offering.title == "6mm/20mil 7x48 SPC Vinyl Click"
        assert offering.sell_price == 0.99

    def test_subject_price_is_sell_price_when_body_has_no_cost_fob(self):
        body = "FOB: Calhoun, GA\nFirst quality SPC with pad. Approx 6 truckloads."

        offering = parse_internal_sheet(SHEET_SUBJECT, body, now=NOW)

        assert offering.sell_price == 0.99
        assert offering.cost_price is None
        assert offering.suggested_sell_note == ""
        assert offering.internal_notes == ""

    def test_headline_becomes_cost_when_body_suggests_a_different_sell(self):
        body = (
            "DELETE RED...FOB: Dallas, TX...Suggested Sell: $9,000/TL...DELETE RED\n"
            "12 pallets per truckload. 3 trucks available."
        )

        offering = parse_internal_sheet("$8,500/TL Porcelain Pavers (New 10/1)", body, now=NOW)

        assert offering.unit == Unit.TRUCKLOAD
        assert offering.sell_price == 9000.0
        assert offering.cost_price == 8500.0
        assert offering.quantity_available == "3 trucks available"
        assert offering.units_per_truckload is None
        assert offering.description == "12 pallets per truckload. 3 trucks available."

    def test_multi_word_subject_price(self):
        body = "FOB: Dallas, TX\nCOST FOB: $8,000 per truckload\nPavers."

        offering = parse_internal_sheet("$8,500 per truckload Porcelain Pavers (AWR 10/1)", body, now=NOW)

        assert offering.title == "Porcelain Pavers"
        assert offering.unit == Unit.TRUCKLOAD
        assert offering.cost_price == 8000.0
        assert offering.sell_price == 8000.0

    def test_body_cost_without_unit_uses_subject_unit(self):
        body = "FOB: Dallas, TX\nCOST FOB: $8000\nPavers."

        offering = parse_internal_sheet("$8,500/TL Porcelain Pavers (AWR 10/1)", body, now=NOW)

        assert offering.cost_price == 8000.0
        assert offering.unit == Unit.TRUCKLOAD

    def test_unparseable_cost_value_is_ignored(self):
        body = "FOB: Dallas, TX\nCOST FOB: 8000 bananas\nPavers."

        offering = parse_internal_sheet("$8,500/TL Porcelain Pavers (AWR 10/1)", body, now=NOW)

        assert offering.cost_price == 8500.0  # from the subject
        assert offering.sell_price == 8500.0

    def test_unitless_cost_is_ignored_when_no_unit_is_known_yet(self):
        body = "FOB: Dallas, TX\nCOST FOB: 8000\nSELL: $9,000/TL\nPavers."

        offering = parse_internal_sheet("Porcelain Pavers (AWR 10/1)", body, now=NOW)

        assert offering.cost_price is None
        assert offering.sell_price == 9000.0
        assert offering.unit == Unit.TRUCKLOAD

    def test_unitless_cost_accepted_once_unit_is_known(self):
        body = "FOB: Dallas, TX\nSELL: $9,000/TL\nCOST FOB: $8000\nPavers."

        offering = parse_internal_sheet("Porcelain Pavers (AWR 10/1)", body, now=NOW)

        assert offering.cost_price == 8000.0
        assert offering.sell_price == 9000.0

    def test_unparseable_sell_value_is_ignored(self):
        body = "FOB: Dallas, TX\nCOST FOB: $8000/TL\nSELL: 9 widgets\nPavers."

        offering = parse_internal_sheet("Porcelain Pavers (AWR 10/1)", body, now=NOW)

        assert offering.sell_price == 8000.0

    def test_body_only_prices_when_subject_has_none(self):
        body = "FOB: Dalton, GA\nCOST: $0.60/sf\nSELL AT $0.75/sf\nCarpet tile."

        offering = parse_internal_sheet("Carpet Tile (New 10/1)", body, now=NOW)

        assert offering.cost_price == 0.60
        assert offering.sell_price == 0.75
        assert offering.unit == Unit.SF

    def test_sell_marker_only_at_line_start(self):
        body = "FOB: Dalton, GA\nCOST FOB: $0.60/sf\nThese sell for $2.50/sf at retail."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.sell_price == 0.60
        assert "retail" in offering.description

    def test_loose_fob_without_colon(self):
        body = "Ships FOB Dalton, GA. Nice carpet tile.\nCOST FOB: $0.60/sf"

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.fob_location == "Dalton, GA"

    def test_fob_variants(self):
        for marker in ("F.O.B.: Dalton, GA", "FOB point: Dalton, GA", "fob - Dalton, GA."):
            offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", f"{marker}\nTile.", now=NOW)
            assert offering.fob_location == "Dalton, GA", marker

    def test_qty_label_wins_over_prose(self):
        body = "FOB: Dalton, GA\nQTY: 48,000 sf (2 truckloads)\nApprox 6 truckloads available."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.quantity_available == "48,000 sf (2 truckloads)"

    def test_quantity_phrases(self):
        cases = {
            "Approx 6 truckloads available.": "Approx 6 truckloads available",
            "We have 2 TL available now.": "2 TL available",
            "About two trucks left.": "About two trucks left",
            "3 loads": "3 loads",
            "Nothing about quantity here.": "",
        }
        for sentence, expected in cases.items():
            offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", f"FOB: Dalton, GA\n{sentence}", now=NOW)
            assert offering.quantity_available == expected, sentence

    def test_units_per_truckload_requires_matching_unit(self):
        body = "FOB: Dalton, GA\n23 pallets per truckload, 24,000 sq. ft. per truck."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.units_per_truckload == 24000.0

    def test_units_per_truckload_plural_square_yards(self):
        body = "FOB: Dalton, GA\nAbout 4,000 sq yds per truck."

        offering = parse_internal_sheet("$11.90/sy Carpet Rolls (AWR 10/1)", body, now=NOW)

        assert offering.unit == Unit.SY
        assert offering.units_per_truckload == 4000.0

    def test_units_per_truckload_absent_when_only_other_units(self):
        body = "FOB: Dalton, GA\n23 pallets per truckload."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.units_per_truckload is None

    def test_units_per_truckload_ignores_zero(self):
        body = "FOB: Dalton, GA\n0 sf per truckload."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.units_per_truckload is None

    def test_unbalanced_delete_red_removes_to_end(self):
        body = "Nice carpet tile.\nDELETE RED FOB: Dalton, GA COST FOB: $0.60/sf bring back all offers"

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.description == "Nice carpet tile."
        assert "COST FOB: $0.60/sf" in offering.internal_notes
        assert offering.fob_location == "Dalton, GA"
        assert offering.make_offers is True

    def test_multiple_suggested_sell_notes_are_joined(self):
        body = "FOB: Dalton, GA\nSuggested Sell Below\nSuggested sell: $0.75/sf\nTile."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.suggested_sell_note == "Suggested Sell Below\nSuggested sell: $0.75/sf"
        assert offering.sell_price == 0.75

    def test_subject_separators_in_body(self):
        body = "DELETE RED; FOB: Dalton, GA; COST FOB: $0.60/sf; DELETE RED. Tile… Approx 3 loads."

        offering = parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", body, now=NOW)

        assert offering.fob_location == "Dalton, GA"
        assert offering.cost_price == 0.60

    def test_empty_body_with_fob_in_subject_fails_clearly(self):
        with pytest.raises(ValueError, match="FOB"):
            parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", "", now=NOW)

    def test_missing_price_raises(self):
        with pytest.raises(ValueError, match="price"):
            parse_internal_sheet("Carpet Tile (New 10/1)", "FOB: Dalton, GA\nTile.", now=NOW)

    def test_missing_fob_raises(self):
        with pytest.raises(ValueError, match="FOB"):
            parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", "COST FOB: $0.60/sf\nTile.", now=NOW)

    def test_cost_fob_is_not_mistaken_for_fob(self):
        with pytest.raises(ValueError, match="FOB"):
            parse_internal_sheet("$0.60/sf Carpet Tile (AWR 10/1)", "COST FOB: Dalton, GA", now=NOW)

    def test_empty_title_raises(self):
        with pytest.raises(ValueError, match="title"):
            parse_internal_sheet("$0.60/sf (AWR 10/1)", "FOB: Dalton, GA", now=NOW)

    def test_none_subject_raises(self):
        with pytest.raises(ValueError, match="title"):
            parse_internal_sheet(None, "FOB: Dalton, GA", now=NOW)  # type: ignore[arg-type]

    def test_none_body_treated_as_empty(self):
        with pytest.raises(ValueError, match="FOB"):
            parse_internal_sheet("$0.60/sf Carpet Tile", None, now=NOW)  # type: ignore[arg-type]

    def test_internal_markup_never_leaks_to_customer_fields(self):
        offering = parse_internal_sheet(SHEET_SUBJECT, SHEET_BODY, now=NOW)

        customer_text = " ".join([offering.title, offering.description, offering.fob_location, offering.quantity_available])
        for marker in INTERNAL_MARKERS:
            assert marker.lower() not in customer_text.lower()

    def test_round_trips_through_to_dict(self):
        offering = parse_internal_sheet(SHEET_SUBJECT, SHEET_BODY, now=NOW)

        restored = Offering.from_dict(json.loads(json.dumps(offering.to_dict())))

        assert restored == offering


# --------------------------------------------------------------------------- #
# load_offering_file
# --------------------------------------------------------------------------- #


def offering_dict(**overrides) -> dict:
    data = {
        "id": "silver-rustic-oak-spc-2026-10-01",
        "title": "Silver Rustic Oak SPC Vinyl Click Flooring",
        "description": "First quality SPC with attached pad.",
        "fob_location": "Calhoun, GA",
        "unit": "sf",
        "sell_price": 0.99,
        "quantity_available": "approx 6 truckloads",
        "units_per_truckload": 20000,
        "make_offers": True,
        "status": "active",
        "created_at": "2026-10-01T14:30:00+00:00",
        "cost_price": 0.80,
        "tags": ["flooring", "spc"],
    }
    data.update(overrides)
    return data


def write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestLoadOfferingFile:
    """Tests for load_offering_file."""

    def test_full_file(self, tmp_path):
        path = write_json(tmp_path / "offering.json", offering_dict())

        offering = load_offering_file(path)

        assert offering.id == "silver-rustic-oak-spc-2026-10-01"
        assert offering.title == "Silver Rustic Oak SPC Vinyl Click Flooring"
        assert offering.unit == Unit.SF
        assert offering.sell_price == 0.99
        assert offering.cost_price == 0.80
        assert offering.status == OfferingStatus.ACTIVE
        assert offering.make_offers is True
        assert offering.units_per_truckload == 20000
        assert offering.tags == ["flooring", "spc"]
        assert offering.created_at == NOW

    def test_accepts_string_path(self, tmp_path):
        path = write_json(tmp_path / "offering.json", offering_dict())

        assert load_offering_file(str(path)).id == "silver-rustic-oak-spc-2026-10-01"

    def test_missing_id_is_generated_from_title_and_utcnow(self, tmp_path):
        data = offering_dict()
        del data["id"]
        path = write_json(tmp_path / "offering.json", data)

        with patch("email_offerings.parsers.utcnow", return_value=NOW):
            offering = load_offering_file(path)

        assert offering.id == make_offering_id("Silver Rustic Oak SPC Vinyl Click Flooring", NOW)
        assert offering.id.endswith("-2026-10-01")

    def test_blank_id_is_generated(self, tmp_path):
        path = write_json(tmp_path / "offering.json", offering_dict(id="   "))

        with patch("email_offerings.parsers.utcnow", return_value=NOW):
            offering = load_offering_file(path)

        assert offering.id == make_offering_id("Silver Rustic Oak SPC Vinyl Click Flooring", NOW)

    def test_minimal_file_defaults(self, tmp_path):
        path = write_json(tmp_path / "min.json", {"title": "Pavers", "fob_location": "Dallas, TX", "unit": "TL", "sell_price": 8500})

        with patch("email_offerings.parsers.utcnow", return_value=NOW):
            offering = load_offering_file(path)

        assert offering.description == ""
        assert offering.status == OfferingStatus.DRAFT
        assert offering.unit == Unit.TRUCKLOAD
        assert offering.id == "pavers-2026-10-01"

    def test_title_is_stripped(self, tmp_path):
        path = write_json(tmp_path / "offering.json", offering_dict(title="  Pavers  "))

        assert load_offering_file(path).title == "Pavers"

    def test_invalid_json_raises_value_error(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ValueError, match="not valid JSON"):
            load_offering_file(path)

    def test_missing_title_raises(self, tmp_path):
        data = offering_dict()
        del data["title"]
        path = write_json(tmp_path / "offering.json", data)

        with pytest.raises(ValueError, match="title"):
            load_offering_file(path)

    def test_blank_title_raises(self, tmp_path):
        path = write_json(tmp_path / "offering.json", offering_dict(title="   "))

        with pytest.raises(ValueError, match="title"):
            load_offering_file(path)

    def test_json_array_raises(self, tmp_path):
        path = write_json(tmp_path / "list.json", [offering_dict()])

        with pytest.raises(ValueError, match="JSON object"):
            load_offering_file(path)

    def test_missing_required_field_raises_value_error(self, tmp_path):
        data = offering_dict()
        del data["sell_price"]
        path = write_json(tmp_path / "offering.json", data)

        with pytest.raises(ValueError, match="sell_price"):
            load_offering_file(path)

    def test_model_validation_errors_include_path(self, tmp_path):
        path = write_json(tmp_path / "offering.json", offering_dict(sell_price=-1))

        with pytest.raises(ValueError, match="offering.json.*Sell price"):
            load_offering_file(path)

    def test_unknown_unit_raises_value_error(self, tmp_path):
        path = write_json(tmp_path / "offering.json", offering_dict(unit="furlongs"))

        with pytest.raises(ValueError, match="Unknown unit"):
            load_offering_file(path)

    def test_missing_file_raises_os_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_offering_file(tmp_path / "nope.json")

    def test_round_trip_with_to_dict(self, tmp_path):
        original = parse_internal_sheet(SHEET_SUBJECT, SHEET_BODY, now=NOW)
        path = write_json(tmp_path / "rt.json", original.to_dict())

        assert load_offering_file(path) == original


# --------------------------------------------------------------------------- #
# parse_buyer_line
# --------------------------------------------------------------------------- #


class TestParseBuyerLine:
    """Tests for parse_buyer_line."""

    def test_name_and_angle_address(self):
        buyer = parse_buyer_line("Jane Doe <jane@example.com>")

        assert isinstance(buyer, Buyer)
        assert buyer.email == "jane@example.com"
        assert buyer.name == "Jane Doe"
        assert buyer.company == ""
        assert buyer.postal_code == ""

    def test_bare_email(self):
        buyer = parse_buyer_line("jane@example.com")

        assert buyer == Buyer(email="jane@example.com", created_at=buyer.created_at)

    def test_csv_style_fields(self):
        buyer = parse_buyer_line("jane@example.com, Jane Doe, Doe Floors, 73127")

        assert buyer.email == "jane@example.com"
        assert buyer.name == "Jane Doe"
        assert buyer.company == "Doe Floors"
        assert buyer.postal_code == "73127"
        assert buyer.city_state == ""
        assert buyer.destination == "73127"

    def test_email_is_lower_cased(self):
        assert parse_buyer_line("Jane Doe <Jane@Example.COM>").email == "jane@example.com"

    def test_angle_form_with_trailing_fields(self):
        buyer = parse_buyer_line('"Doe, Jane" <jane@example.com>, Doe Floors, 73127')

        assert buyer.name == "Doe, Jane"
        assert buyer.company == "Doe Floors"
        assert buyer.postal_code == "73127"

    def test_angle_form_without_name(self):
        buyer = parse_buyer_line("<jane@example.com>")

        assert buyer.email == "jane@example.com"
        assert buyer.name == ""

    def test_comment_style_name(self):
        buyer = parse_buyer_line("jane@example.com (Jane Doe)")

        assert buyer.email == "jane@example.com"
        assert buyer.name == "Jane Doe"

    def test_city_state_in_location_field(self):
        buyer = parse_buyer_line("jane@example.com, Jane Doe, Doe Floors, Oklahoma City, OK")

        assert buyer.postal_code == ""
        assert buyer.city_state == "Oklahoma City, OK"
        assert buyer.destination == "Oklahoma City, OK"

    def test_zip_and_city_state(self):
        buyer = parse_buyer_line("jane@example.com, Jane Doe, Doe Floors, 73127, Oklahoma City, OK")

        assert buyer.postal_code == "73127"
        assert buyer.city_state == "Oklahoma City, OK"

    def test_zip_plus_four(self):
        assert parse_buyer_line("jane@example.com, Jane, Co, 73127-1234").postal_code == "73127-1234"

    def test_semicolon_and_tab_separators(self):
        assert parse_buyer_line("jane@example.com; Jane Doe; Doe Floors").company == "Doe Floors"
        assert parse_buyer_line("jane@example.com\tJane Doe\tDoe Floors").name == "Jane Doe"

    def test_email_in_later_field(self):
        buyer = parse_buyer_line("Jane Doe, jane@example.com, Doe Floors")

        assert buyer.email == "jane@example.com"
        assert buyer.name == "Jane Doe"
        assert buyer.company == "Doe Floors"

    def test_blank_fields_are_kept_positional(self):
        buyer = parse_buyer_line("jane@example.com,, Doe Floors, 73127")

        assert buyer.name == ""
        assert buyer.company == "Doe Floors"
        assert buyer.postal_code == "73127"

    def test_blank_trailing_field_is_skipped(self):
        buyer = parse_buyer_line("jane@example.com, Jane Doe, Doe Floors, , 73127")

        assert buyer.postal_code == "73127"
        assert buyer.city_state == ""

    def test_quoted_fields_are_unquoted(self):
        buyer = parse_buyer_line('"jane@example.com", "Jane Doe", "Doe Floors"')

        assert buyer.email == "jane@example.com"
        assert buyer.name == "Jane Doe"
        assert buyer.company == "Doe Floors"

    def test_surrounding_whitespace(self):
        assert parse_buyer_line("   jane@example.com   \n").email == "jane@example.com"

    @pytest.mark.parametrize("line", ["", "   ", "\n", None])
    def test_blank_returns_none(self, line):
        assert parse_buyer_line(line) is None  # type: ignore[arg-type]

    def test_comment_line_returns_none(self):
        assert parse_buyer_line("# jane@example.com") is None

    @pytest.mark.parametrize(
        "line",
        ["not an email", "jane@example", "Jane Doe", "<>", "Jane <bad>", "Jane <jane@example>", "@example.com", "jane@@example.com"],
    )
    def test_invalid_returns_none(self, line):
        assert parse_buyer_line(line) is None

    def test_header_row_returns_none(self):
        assert parse_buyer_line("email,name,company,postal_code") is None

    def test_new_buyer_is_active(self):
        buyer = parse_buyer_line("jane@example.com")

        assert buyer.status.value == "active"
        assert buyer.tags == []
