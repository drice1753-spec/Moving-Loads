"""Tests for freight calculator module."""

import pytest
from unittest.mock import patch, MagicMock

from moving_loads.freight_calculator import (
    FreightResult,
    get_road_distance,
    calculate_freight,
    DEFAULT_RATE_PER_MILE,
    _METERS_PER_MILE,
)


def _mock_api_response(distance_meters, status="OK", element_status="OK"):
    """Build a mock response mimicking Google Distance Matrix API."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "status": status,
        "origin_addresses": ["New York, NY, USA"],
        "destination_addresses": ["Los Angeles, CA, USA"],
        "rows": [
            {
                "elements": [
                    {
                        "status": element_status,
                        "distance": {"value": distance_meters, "text": "..."},
                        "duration": {"value": 0, "text": "..."},
                    }
                ]
            }
        ],
    }
    return mock_resp


class TestFreightResult:
    """Tests for the FreightResult dataclass."""

    def test_create_result(self):
        result = FreightResult(
            origin="NYC",
            destination="LA",
            distance_miles=100.0,
            rate_per_mile=4.50,
            total_cost=450.0,
        )
        assert result.origin == "NYC"
        assert result.destination == "LA"
        assert result.distance_miles == 100.0
        assert result.rate_per_mile == 4.50
        assert result.total_cost == 450.0

    def test_negative_distance_raises(self):
        with pytest.raises(ValueError, match="Distance"):
            FreightResult("A", "B", distance_miles=-1.0, rate_per_mile=4.50, total_cost=0.0)

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="Rate per mile"):
            FreightResult("A", "B", distance_miles=10.0, rate_per_mile=-1.0, total_cost=0.0)

    def test_negative_cost_raises(self):
        with pytest.raises(ValueError, match="Total cost"):
            FreightResult("A", "B", distance_miles=10.0, rate_per_mile=4.50, total_cost=-1.0)


class TestGetRoadDistance:
    """Tests for the get_road_distance function."""

    @patch("moving_loads.freight_calculator.requests.get")
    def test_valid_distance(self, mock_get):
        distance_meters = 400000.0  # ~248.55 miles
        mock_get.return_value = _mock_api_response(distance_meters)

        result = get_road_distance("New York, NY", "Philadelphia, PA", "fake-key")

        assert result == pytest.approx(distance_meters / _METERS_PER_MILE, rel=1e-6)
        mock_get.assert_called_once()

    def test_empty_origin_raises(self):
        with pytest.raises(ValueError, match="Origin"):
            get_road_distance("", "LA", "key")

    def test_empty_destination_raises(self):
        with pytest.raises(ValueError, match="Destination"):
            get_road_distance("NYC", "", "key")

    def test_empty_api_key_raises(self):
        with pytest.raises(ValueError, match="API key"):
            get_road_distance("NYC", "LA", "")

    @patch("moving_loads.freight_calculator.requests.get")
    def test_api_top_level_error(self, mock_get):
        mock_get.return_value = _mock_api_response(0, status="REQUEST_DENIED")

        with pytest.raises(ValueError, match="API error"):
            get_road_distance("NYC", "LA", "bad-key")

    @patch("moving_loads.freight_calculator.requests.get")
    def test_no_route_found(self, mock_get):
        mock_get.return_value = _mock_api_response(0, element_status="ZERO_RESULTS")

        with pytest.raises(ValueError, match="No route found"):
            get_road_distance("NYC", "Atlantis", "key")

    @patch("moving_loads.freight_calculator.requests.get")
    def test_http_error(self, mock_get):
        import requests

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        mock_get.return_value = mock_resp

        with pytest.raises(requests.exceptions.HTTPError):
            get_road_distance("NYC", "LA", "key")


class TestCalculateFreight:
    """Tests for the calculate_freight function."""

    @patch("moving_loads.freight_calculator.get_road_distance", return_value=100.0)
    def test_default_rate(self, mock_dist):
        result = calculate_freight("NYC", "LA", "key")

        assert result.total_cost == pytest.approx(450.0)
        assert result.rate_per_mile == DEFAULT_RATE_PER_MILE
        assert result.distance_miles == 100.0

    @patch("moving_loads.freight_calculator.get_road_distance", return_value=100.0)
    def test_custom_rate(self, mock_dist):
        result = calculate_freight("NYC", "LA", "key", rate_per_mile=6.0)

        assert result.total_cost == pytest.approx(600.0)
        assert result.rate_per_mile == 6.0

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="Rate per mile"):
            calculate_freight("NYC", "LA", "key", rate_per_mile=-1.0)

    @patch("moving_loads.freight_calculator.get_road_distance", return_value=100.0)
    def test_result_fields(self, mock_dist):
        result = calculate_freight("  NYC  ", "  LA  ", "key")

        assert result.origin == "NYC"
        assert result.destination == "LA"
        assert result.distance_miles == 100.0
        assert result.rate_per_mile == 4.50
        assert result.total_cost == 450.0

    @patch("moving_loads.freight_calculator.get_road_distance", return_value=123.456)
    def test_rounding(self, mock_dist):
        result = calculate_freight("A", "B", "key", rate_per_mile=3.33)

        assert result.distance_miles == 123.46
        assert result.total_cost == round(123.46 * 3.33, 2)
