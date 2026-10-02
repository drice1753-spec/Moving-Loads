"""Tests for email_offerings.pricing (delivered pricing; no network)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from email_offerings.models import DeliveredQuote, Offering, Unit
from email_offerings.pricing import (
    DistanceProvider,
    freight_for_truckload,
    google_distance_provider,
    normalize_destination,
    quote_delivered,
    round_price,
)

NOW = datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc)

# Worked example from the design contract: Calhoun, GA → 73127 is 790 road miles
# at $3.67/mi → $2,899 per truckload; 20,000 sf per truck → $0.99/sf becomes $1.13/sf.
WORKED_MILES = 790.0
WORKED_RATE = 3.67


def make_offering(**overrides) -> Offering:
    data = dict(
        id="silver-rustic-oak-spc-2026-10-01",
        title="Silver Rustic Oak SPC Vinyl Click Flooring",
        description="All first quality product with an attached pad.",
        fob_location="Calhoun, GA",
        unit=Unit.SF,
        sell_price=0.99,
        units_per_truckload=20000,
        quantity_available="approx 6 truckloads",
    )
    data.update(overrides)
    return Offering(**data)


def fixed_distance(miles: float) -> DistanceProvider:
    """A distance provider that always answers ``miles``."""
    return lambda origin, destination: miles


class TestRoundPrice:
    """Tests for round_price."""

    @pytest.mark.parametrize("unit", [Unit.SF, Unit.SY, Unit.LF, Unit.EACH, Unit.PALLET])
    def test_per_unit_prices_round_to_cents(self, unit):
        assert round_price(1.13495, unit) == 1.13
        assert round_price(11.9, unit) == 11.9

    def test_truckload_rounds_to_whole_dollars(self):
        assert round_price(9700.4, Unit.TRUCKLOAD) == 9700.0
        assert round_price(9700.5, Unit.TRUCKLOAD) == 9701.0

    def test_halves_round_up_not_bankers(self):
        assert round_price(1.005, Unit.SF) == 1.01
        assert round_price(0.125, Unit.SF) == 0.13

    def test_accepts_unit_string(self):
        assert round_price(1.239, "sf") == 1.24
        assert round_price(1.239, "/TL") == 1.0

    def test_returns_float(self):
        assert isinstance(round_price(8500, Unit.TRUCKLOAD), float)


class TestNormalizeDestination:
    """Tests for normalize_destination."""

    def test_zip_gets_usa_suffix(self):
        assert normalize_destination("73127", None) == "73127, USA"

    def test_zip_plus_four(self):
        assert normalize_destination("73127-1234", "") == "73127-1234, USA"

    def test_zip_is_stripped(self):
        assert normalize_destination("  73127 \n", None) == "73127, USA"

    def test_city_state_unchanged(self):
        assert normalize_destination(None, "Oklahoma City, OK") == "Oklahoma City, OK"

    def test_city_state_stripped(self):
        assert normalize_destination("", "  Oklahoma City, OK  ") == "Oklahoma City, OK"

    def test_postal_code_wins_over_city_state(self):
        assert normalize_destination("73127", "Oklahoma City, OK") == "73127, USA"

    def test_non_us_postal_code_returned_as_is(self):
        assert normalize_destination(" T5K 2M5 ", "Edmonton, AB") == "T5K 2M5"

    def test_already_normalised_zip_not_doubled(self):
        assert normalize_destination("73127, USA", None) == "73127, USA"

    def test_both_none_raises(self):
        with pytest.raises(ValueError, match="destination"):
            normalize_destination(None, None)

    def test_both_blank_raises(self):
        with pytest.raises(ValueError, match="destination"):
            normalize_destination("   ", "")


class TestGoogleDistanceProvider:
    """Tests for google_distance_provider (the Google call itself is mocked)."""

    @patch("email_offerings.pricing.get_road_distance", return_value=790.4)
    def test_threads_api_key_through(self, mock_distance):
        provider = google_distance_provider("fake-key")

        miles = provider("Calhoun, GA", "73127, USA")

        assert miles == 790.4
        mock_distance.assert_called_once_with("Calhoun, GA", "73127, USA", "fake-key")

    @patch("email_offerings.pricing.get_road_distance", return_value=10.0)
    def test_api_key_is_stripped(self, mock_distance):
        provider = google_distance_provider("  fake-key  ")

        provider("A", "B")

        assert mock_distance.call_args.args[2] == "fake-key"

    @patch("email_offerings.pricing.get_road_distance")
    def test_provider_is_lazy(self, mock_distance):
        google_distance_provider("fake-key")

        mock_distance.assert_not_called()

    @patch("email_offerings.pricing.get_road_distance", side_effect=ValueError("No route found: ZERO_RESULTS"))
    def test_errors_propagate(self, mock_distance):
        provider = google_distance_provider("fake-key")

        with pytest.raises(ValueError, match="No route"):
            provider("Calhoun, GA", "Atlantis")

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="API key"):
            google_distance_provider("")

    def test_blank_key_raises(self):
        with pytest.raises(ValueError, match="API key"):
            google_distance_provider("   ")

    @patch("moving_loads.freight_calculator.requests.get")
    def test_no_http_call_until_provider_is_used(self, mock_get):
        google_distance_provider("fake-key")

        mock_get.assert_not_called()


class TestFreightForTruckload:
    """Tests for freight_for_truckload."""

    def test_worked_example(self):
        miles, freight = freight_for_truckload(
            "Calhoun, GA", "73127, USA", rate_per_mile=WORKED_RATE, distance_provider=fixed_distance(WORKED_MILES)
        )

        assert miles == 790.0
        assert freight == 2899.0

    def test_distance_rounded_to_one_decimal(self):
        miles, freight = freight_for_truckload("A", "B", rate_per_mile=3.67, distance_provider=fixed_distance(123.456))

        assert miles == 123.5
        assert freight == 453.0  # 123.5 * 3.67 = 453.245

    def test_freight_rounds_half_up_to_whole_dollars(self):
        _, freight = freight_for_truckload("A", "B", rate_per_mile=1.0, distance_provider=fixed_distance(100.5))

        assert freight == 101.0

    def test_margin_added_per_truckload(self):
        _, freight = freight_for_truckload(
            "A", "B", rate_per_mile=WORKED_RATE, distance_provider=fixed_distance(WORKED_MILES), margin_per_truckload=150
        )

        assert freight == 3049.0

    def test_provider_receives_stripped_strings(self):
        provider = MagicMock(return_value=100.0)

        freight_for_truckload("  Calhoun, GA ", " 73127, USA\n", rate_per_mile=4.5, distance_provider=provider)

        provider.assert_called_once_with("Calhoun, GA", "73127, USA")

    def test_integer_distance_from_provider_is_accepted(self):
        miles, freight = freight_for_truckload("A", "B", rate_per_mile=2.0, distance_provider=lambda o, d: 300)

        assert miles == 300.0
        assert freight == 600.0

    def test_zero_rate_gives_margin_only(self):
        _, freight = freight_for_truckload(
            "A", "B", rate_per_mile=0.0, distance_provider=fixed_distance(500), margin_per_truckload=200
        )

        assert freight == 200.0

    def test_empty_origin_raises(self):
        with pytest.raises(ValueError, match="Origin"):
            freight_for_truckload("  ", "B", rate_per_mile=4.5, distance_provider=fixed_distance(1))

    def test_empty_destination_raises(self):
        with pytest.raises(ValueError, match="Destination"):
            freight_for_truckload("A", "", rate_per_mile=4.5, distance_provider=fixed_distance(1))

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="Rate per mile"):
            freight_for_truckload("A", "B", rate_per_mile=-1.0, distance_provider=fixed_distance(1))

    def test_negative_margin_raises(self):
        with pytest.raises(ValueError, match="margin"):
            freight_for_truckload("A", "B", rate_per_mile=1.0, distance_provider=fixed_distance(1), margin_per_truckload=-5)

    def test_negative_distance_from_provider_raises(self):
        with pytest.raises(ValueError, match="negative distance"):
            freight_for_truckload("A", "B", rate_per_mile=1.0, distance_provider=fixed_distance(-3))

    def test_provider_errors_propagate(self):
        def broken(origin: str, destination: str) -> float:
            raise ValueError("No route found: ZERO_RESULTS")

        with pytest.raises(ValueError, match="No route"):
            freight_for_truckload("A", "Atlantis", rate_per_mile=1.0, distance_provider=broken)


class TestQuoteDelivered:
    """Tests for quote_delivered."""

    def test_worked_example_two_truckloads(self):
        quote = quote_delivered(
            make_offering(),
            "73127, USA",
            truckloads=2,
            rate_per_mile=WORKED_RATE,
            distance_provider=fixed_distance(WORKED_MILES),
            now=NOW,
        )

        assert isinstance(quote, DeliveredQuote)
        assert quote.offering_id == "silver-rustic-oak-spc-2026-10-01"
        assert quote.origin == "Calhoun, GA"
        assert quote.destination == "73127, USA"
        assert quote.distance_miles == 790.0
        assert quote.rate_per_mile == WORKED_RATE
        assert quote.truckloads == 2.0
        assert quote.freight_per_truckload == 2899.0
        assert quote.freight_total == 5798.0
        assert quote.unit == Unit.SF
        assert quote.fob_price_per_unit == 0.99
        assert quote.delivered_price_per_unit == pytest.approx(1.13)
        assert quote.units_per_truckload == 20000
        assert quote.units_total == 40000.0
        assert quote.delivered_total == pytest.approx(45200.0)
        assert quote.quoted_at == NOW

    def test_single_truckload_default(self):
        quote = quote_delivered(
            make_offering(), "73127, USA", rate_per_mile=WORKED_RATE, distance_provider=fixed_distance(WORKED_MILES)
        )

        assert quote.truckloads == 1.0
        assert quote.freight_total == 2899.0
        assert quote.units_total == 20000.0
        assert quote.delivered_price_per_unit == pytest.approx(1.13)
        assert quote.delivered_total == pytest.approx(22600.0)

    def test_truckload_unit_offering(self):
        offering = make_offering(unit=Unit.TRUCKLOAD, sell_price=8500, units_per_truckload=None)

        quote = quote_delivered(offering, "Oklahoma City, OK", rate_per_mile=4.0, distance_provider=fixed_distance(300))

        assert quote.freight_per_truckload == 1200.0
        assert quote.delivered_price_per_unit == 9700.0
        assert quote.units_total == 1.0
        assert quote.units_per_truckload is None
        assert quote.delivered_total == 9700.0
        assert quote.unit == Unit.TRUCKLOAD

    def test_truckload_unit_multiple_trucks(self):
        offering = make_offering(unit=Unit.TRUCKLOAD, sell_price=8500, units_per_truckload=None)

        quote = quote_delivered(
            offering, "Oklahoma City, OK", truckloads=3, rate_per_mile=4.0, distance_provider=fixed_distance(300)
        )

        assert quote.delivered_price_per_unit == 9700.0
        assert quote.freight_total == 3600.0
        assert quote.units_total == 3.0
        assert quote.delivered_total == 29100.0

    def test_truckload_unit_price_rounds_to_whole_dollars(self):
        offering = make_offering(unit=Unit.TRUCKLOAD, sell_price=8500.25, units_per_truckload=None)

        quote = quote_delivered(offering, "B", rate_per_mile=1.0, distance_provider=fixed_distance(100))

        assert quote.delivered_price_per_unit == 8600.0

    def test_square_yard_offering(self):
        offering = make_offering(unit=Unit.SY, sell_price=11.90, units_per_truckload=4000)

        quote = quote_delivered(offering, "B", rate_per_mile=4.0, distance_provider=fixed_distance(500))

        assert quote.freight_per_truckload == 2000.0
        assert quote.delivered_price_per_unit == pytest.approx(12.40)
        assert quote.units_total == 4000.0

    def test_margin_is_included(self):
        quote = quote_delivered(
            make_offering(),
            "73127, USA",
            rate_per_mile=WORKED_RATE,
            distance_provider=fixed_distance(WORKED_MILES),
            margin_per_truckload=101,  # 2899 + 101 = 3000 → 0.15/sf
        )

        assert quote.freight_per_truckload == 3000.0
        assert quote.delivered_price_per_unit == pytest.approx(1.14)

    def test_fractional_truckloads(self):
        quote = quote_delivered(
            make_offering(), "73127, USA", truckloads=1.5, rate_per_mile=WORKED_RATE, distance_provider=fixed_distance(WORKED_MILES)
        )

        assert quote.freight_total == 4349.0  # 2899 * 1.5 = 4348.5 rounded half up
        assert quote.units_total == 30000.0

    def test_zero_distance_delivered_equals_fob(self):
        quote = quote_delivered(make_offering(), "Calhoun, GA", rate_per_mile=WORKED_RATE, distance_provider=fixed_distance(0))

        assert quote.freight_per_truckload == 0.0
        assert quote.delivered_price_per_unit == 0.99
        assert quote.distance_miles == 0.0

    def test_delivered_never_rounds_below_fob(self):
        # 0.994 + tiny freight rounds to 0.99 (< 0.994); the quote must clamp to FOB.
        offering = make_offering(sell_price=0.994, units_per_truckload=1_000_000)

        quote = quote_delivered(offering, "B", rate_per_mile=1.0, distance_provider=fixed_distance(10))

        assert quote.delivered_price_per_unit >= quote.fob_price_per_unit

    def test_destination_and_origin_are_stripped(self):
        provider = MagicMock(return_value=100.0)
        offering = make_offering(fob_location="  Dalton, GA ")

        quote = quote_delivered(offering, "  Oklahoma City, OK ", rate_per_mile=1.0, distance_provider=provider)

        provider.assert_called_once_with("Dalton, GA", "Oklahoma City, OK")
        assert quote.origin == "Dalton, GA"
        assert quote.destination == "Oklahoma City, OK"

    def test_quoted_at_defaults_to_now(self):
        before = datetime.now(timezone.utc)

        quote = quote_delivered(make_offering(), "B", rate_per_mile=1.0, distance_provider=fixed_distance(10))

        assert before <= quote.quoted_at <= datetime.now(timezone.utc)

    def test_distance_provider_called_once(self):
        provider = MagicMock(return_value=WORKED_MILES)

        quote_delivered(make_offering(), "73127, USA", truckloads=2, rate_per_mile=WORKED_RATE, distance_provider=provider)

        assert provider.call_count == 1

    def test_missing_units_per_truckload_raises(self):
        offering = make_offering(units_per_truckload=None)

        with pytest.raises(ValueError, match="units_per_truckload"):
            quote_delivered(offering, "73127, USA", rate_per_mile=WORKED_RATE, distance_provider=fixed_distance(WORKED_MILES))

    def test_zero_truckloads_raises(self):
        with pytest.raises(ValueError, match="Truckloads"):
            quote_delivered(make_offering(), "B", truckloads=0, rate_per_mile=1.0, distance_provider=fixed_distance(10))

    def test_negative_truckloads_raises(self):
        with pytest.raises(ValueError, match="Truckloads"):
            quote_delivered(make_offering(), "B", truckloads=-1, rate_per_mile=1.0, distance_provider=fixed_distance(10))

    def test_empty_destination_raises(self):
        with pytest.raises(ValueError, match="Destination"):
            quote_delivered(make_offering(), "   ", rate_per_mile=1.0, distance_provider=fixed_distance(10))

    def test_provider_failure_propagates(self):
        def broken(origin: str, destination: str) -> float:
            raise RuntimeError("network down")

        with pytest.raises(RuntimeError, match="network down"):
            quote_delivered(make_offering(), "B", rate_per_mile=1.0, distance_provider=broken)

    def test_end_to_end_with_google_provider_mocked(self):
        with patch("email_offerings.pricing.get_road_distance", return_value=WORKED_MILES) as mock_distance:
            provider = google_distance_provider("fake-key")
            quote = quote_delivered(
                make_offering(),
                normalize_destination("73127", None),
                truckloads=2,
                rate_per_mile=WORKED_RATE,
                distance_provider=provider,
            )

        mock_distance.assert_called_once_with("Calhoun, GA", "73127, USA", "fake-key")
        assert quote.delivered_price_per_unit == pytest.approx(1.13)
        assert quote.freight_total == 5798.0
