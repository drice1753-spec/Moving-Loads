"""Delivered pricing on top of ``moving_loads.freight_calculator``.

The business quotes product FOB (free on board) at the warehouse; a buyer who
asks *"how cheap can you get on 2 truckloads delivered to 73127?"* needs the
FOB price plus freight. Freight is ``road miles x rate per mile`` per
truckload, optionally with a fixed margin per truckload, and is spread over
the number of units a truck carries for per-unit prices.

Road distance comes from a :data:`DistanceProvider`: any callable
``(origin, destination) -> miles``. :func:`google_distance_provider` wraps the
Google Distance Matrix lookup in ``moving_loads.freight_calculator``; tests
pass a plain lambda so no network call is ever made.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from email_offerings.models import DeliveredQuote, Offering, Unit, utcnow
from moving_loads.freight_calculator import get_road_distance

DistanceProvider = Callable[[str, str], float]
"""``(origin, destination) -> road miles``."""

_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _round_half_up(value: float, places: int) -> float:
    """Round ``value`` to ``places`` decimals, halves away from zero.

    Python's built-in ``round`` uses banker's rounding, which turns a price of
    ``1.005`` into ``1.0``. Prices and freight dollars are rounded the way a
    person with a calculator would.
    """
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _coerce_unit(unit: Unit | str) -> Unit:
    return unit if isinstance(unit, Unit) else Unit.parse(str(unit))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def google_distance_provider(api_key: str) -> DistanceProvider:
    """Build a :data:`DistanceProvider` backed by the Google Distance Matrix API.

    Args:
        api_key: Google Maps API key, threaded through to
            :func:`moving_loads.freight_calculator.get_road_distance`.

    Returns:
        A callable ``(origin, destination) -> miles``.

    Raises:
        ValueError: If ``api_key`` is empty.
    """
    if not api_key or not api_key.strip():
        raise ValueError("Google Maps API key must not be empty.")
    key = api_key.strip()

    def provider(origin: str, destination: str) -> float:
        return get_road_distance(origin, destination, key)

    return provider


def normalize_destination(postal_code: str | None, city_state: str | None) -> str:
    """Turn what a buyer told us into a geocodable destination string.

    A US ZIP (``73127`` or ``73127-1234``) becomes ``"73127, USA"`` so the
    Distance Matrix API does not confuse it with a postal code elsewhere.
    Anything else is returned stripped of surrounding whitespace. The postal
    code wins over ``city_state`` when both are given because it is the more
    precise of the two.

    Args:
        postal_code: ZIP code (may be ``None`` or blank).
        city_state: ``"Oklahoma City, OK"`` style fallback.

    Returns:
        The destination string to hand to a :data:`DistanceProvider`.

    Raises:
        ValueError: If both inputs are empty.
    """
    postal = (postal_code or "").strip()
    place = (city_state or "").strip()
    if postal:
        if _ZIP_RE.match(postal):
            return f"{postal}, USA"
        return postal
    if place:
        return place
    raise ValueError("A destination is required: give a ZIP code or a 'City, ST'.")


def round_price(value: float, unit: Unit) -> float:
    """Round a price the way it is quoted for ``unit``.

    Per-unit prices (``sf``, ``sy``, ``lf``, ``ea``, ``plt``) are quoted to the
    cent; truckload prices are quoted in whole dollars.

    Args:
        value: Unrounded price.
        unit: Pricing unit (a :class:`Unit` or its string value).

    Returns:
        The rounded price as a float.
    """
    places = 0 if _coerce_unit(unit) == Unit.TRUCKLOAD else 2
    return _round_half_up(value, places)


def freight_for_truckload(
    origin: str,
    destination: str,
    *,
    rate_per_mile: float,
    distance_provider: DistanceProvider,
    margin_per_truckload: float = 0.0,
) -> tuple[float, float]:
    """Look up the road distance and price one truckload of freight.

    Args:
        origin: FOB point, e.g. ``"Calhoun, GA"``.
        destination: Buyer destination, e.g. ``"73127, USA"``.
        rate_per_mile: Carrier rate in dollars per mile.
        distance_provider: ``(origin, destination) -> miles``.
        margin_per_truckload: Fixed dollars added to each truckload.

    Returns:
        ``(distance_miles, freight)`` with the distance rounded to one decimal
        and the freight (``miles * rate + margin``) rounded to whole dollars.

    Raises:
        ValueError: If an input is empty or negative, or the provider returns
            a negative distance. Exceptions raised by the provider (no route,
            HTTP failures) propagate unchanged.
    """
    if not origin or not origin.strip():
        raise ValueError("Origin must not be empty.")
    if not destination or not destination.strip():
        raise ValueError("Destination must not be empty.")
    if rate_per_mile < 0:
        raise ValueError("Rate per mile must be non-negative.")
    if margin_per_truckload < 0:
        raise ValueError("Freight margin must be non-negative.")

    raw_miles = float(distance_provider(origin.strip(), destination.strip()))
    if raw_miles < 0:
        raise ValueError(f"Distance provider returned a negative distance: {raw_miles!r}.")

    miles = round(raw_miles, 1)
    freight = _round_half_up(miles * rate_per_mile + margin_per_truckload, 0)
    return miles, freight


def quote_delivered(
    offering: Offering,
    destination: str,
    *,
    truckloads: float = 1.0,
    rate_per_mile: float,
    distance_provider: DistanceProvider,
    margin_per_truckload: float = 0.0,
    now: datetime | None = None,
) -> DeliveredQuote:
    """Quote an offering delivered to ``destination``.

    For an offering priced per truckload the delivered price is simply
    ``sell_price + freight_per_truckload``. For any other unit the freight for
    one truckload is spread over ``offering.units_per_truckload`` units and
    added to the FOB price, then rounded with :func:`round_price`.

    Args:
        offering: The offering being quoted; ``fob_location`` is the origin.
        destination: Normalised destination (see :func:`normalize_destination`).
        truckloads: Number of truckloads requested (may be fractional).
        rate_per_mile: Carrier rate in dollars per mile.
        distance_provider: ``(origin, destination) -> miles``.
        margin_per_truckload: Fixed dollars added to each truckload's freight.
        now: Timestamp for ``quoted_at`` (defaults to the current UTC time).

    Returns:
        A fully populated :class:`DeliveredQuote`.

    Raises:
        ValueError: If ``truckloads`` is not positive, ``destination`` is
            empty, or a per-unit offering has no ``units_per_truckload``.
    """
    if truckloads <= 0:
        raise ValueError("Truckloads must be positive.")
    if not destination or not destination.strip():
        raise ValueError("Destination must not be empty.")

    origin = offering.fob_location.strip()
    destination = destination.strip()
    miles, freight_per_truckload = freight_for_truckload(
        origin,
        destination,
        rate_per_mile=rate_per_mile,
        distance_provider=distance_provider,
        margin_per_truckload=margin_per_truckload,
    )
    freight_total = _round_half_up(freight_per_truckload * truckloads, 0)
    fob_price = float(offering.sell_price)

    if offering.unit == Unit.TRUCKLOAD:
        units_per_truckload = offering.units_per_truckload
        delivered_per_unit = round_price(fob_price + freight_per_truckload, Unit.TRUCKLOAD)
        units_total = float(truckloads)
        delivered_total = _round_half_up(delivered_per_unit * units_total, 2)
    else:
        units_per_truckload = offering.units_per_truckload
        if units_per_truckload is None:
            raise ValueError(
                f"Offering {offering.id!r} is priced per {offering.unit.value} but has no "
                "units_per_truckload; set it before quoting delivered prices."
            )
        delivered_per_unit = round_price(fob_price + freight_per_truckload / units_per_truckload, offering.unit)
        units_total = float(truckloads) * float(units_per_truckload)
        delivered_total = _round_half_up(delivered_per_unit * units_total, 2)

    # Rounding can never be allowed to quote below FOB.
    delivered_per_unit = max(delivered_per_unit, fob_price)

    return DeliveredQuote(
        offering_id=offering.id,
        origin=origin,
        destination=destination,
        distance_miles=miles,
        rate_per_mile=float(rate_per_mile),
        truckloads=float(truckloads),
        freight_per_truckload=freight_per_truckload,
        freight_total=freight_total,
        unit=offering.unit,
        fob_price_per_unit=fob_price,
        delivered_price_per_unit=delivered_per_unit,
        units_per_truckload=units_per_truckload,
        units_total=units_total,
        delivered_total=delivered_total,
        quoted_at=now or utcnow(),
    )


__all__ = [
    "DistanceProvider",
    "freight_for_truckload",
    "google_distance_provider",
    "normalize_destination",
    "quote_delivered",
    "round_price",
]
