"""Freight cost calculation based on road distance."""

from __future__ import annotations

from dataclasses import dataclass

import requests

DEFAULT_RATE_PER_MILE: float = 4.50
_DISTANCE_MATRIX_URL: str = "https://maps.googleapis.com/maps/api/distancematrix/json"
_METERS_PER_MILE: float = 1609.344


@dataclass
class FreightResult:
    """Result of a freight cost calculation."""

    origin: str
    destination: str
    distance_miles: float
    rate_per_mile: float
    total_cost: float

    def __post_init__(self) -> None:
        if self.distance_miles < 0:
            raise ValueError("Distance must be non-negative.")
        if self.rate_per_mile < 0:
            raise ValueError("Rate per mile must be non-negative.")
        if self.total_cost < 0:
            raise ValueError("Total cost must be non-negative.")


def get_road_distance(
    origin: str,
    destination: str,
    api_key: str,
) -> float:
    """Query the Google Maps Distance Matrix API for road distance in miles.

    Args:
        origin: Origin city/state or address string.
        destination: Destination city/state or address string.
        api_key: Google Maps API key.

    Returns:
        Road distance in miles.

    Raises:
        ValueError: If inputs are empty or the API returns an error.
        requests.RequestException: If the HTTP request fails.
    """
    if not origin or not origin.strip():
        raise ValueError("Origin must not be empty.")
    if not destination or not destination.strip():
        raise ValueError("Destination must not be empty.")
    if not api_key or not api_key.strip():
        raise ValueError("API key must not be empty.")

    params = {
        "origins": origin.strip(),
        "destinations": destination.strip(),
        "units": "imperial",
        "key": api_key.strip(),
    }

    response = requests.get(_DISTANCE_MATRIX_URL, params=params)
    response.raise_for_status()

    data = response.json()

    if data.get("status") != "OK":
        raise ValueError(f"API error: {data.get('status')}")

    element = data["rows"][0]["elements"][0]

    if element.get("status") != "OK":
        raise ValueError(f"No route found: {element.get('status')}")

    distance_meters = element["distance"]["value"]
    return distance_meters / _METERS_PER_MILE


def calculate_freight(
    origin: str,
    destination: str,
    api_key: str,
    rate_per_mile: float = DEFAULT_RATE_PER_MILE,
) -> FreightResult:
    """Calculate freight cost between two locations.

    Args:
        origin: Origin city/state or address string.
        destination: Destination city/state or address string.
        api_key: Google Maps API key.
        rate_per_mile: Cost per mile in dollars (default $4.50).

    Returns:
        A FreightResult with distance and cost details.

    Raises:
        ValueError: If inputs are invalid or no route is found.
        requests.RequestException: If the HTTP request fails.
    """
    if rate_per_mile < 0:
        raise ValueError("Rate per mile must be non-negative.")

    distance_miles = get_road_distance(origin, destination, api_key)
    distance_miles = round(distance_miles, 2)
    total_cost = round(distance_miles * rate_per_mile, 2)

    return FreightResult(
        origin=origin.strip(),
        destination=destination.strip(),
        distance_miles=distance_miles,
        rate_per_mile=rate_per_mile,
        total_cost=total_cost,
    )
