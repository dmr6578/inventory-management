"""
Tests for the Restocking tab's API endpoints: recommendations and order submission.
"""
import pytest


class TestRestockRecommendationsEndpoint:
    """Test suite for GET /api/restock/recommendations."""

    def test_get_recommendations_default(self, client):
        """Test getting recommendations with no budget specified."""
        response = client.get("/api/restock/recommendations")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        item = data[0]
        for field in [
            "sku", "name", "category", "warehouse", "quantity_on_hand",
            "reorder_point", "current_demand", "forecasted_demand", "trend",
            "unit_cost", "recommended_quantity", "recommended_cost",
            "urgent", "default_selected"
        ]:
            assert field in item

    def test_recommendations_only_need_restocking(self, client):
        """Every recommended item should actually need more stock than it has."""
        response = client.get("/api/restock/recommendations")
        data = response.json()

        for item in data:
            assert item["recommended_quantity"] > 0
            assert item["forecasted_demand"] - item["quantity_on_hand"] == item["recommended_quantity"]

    def test_recommendations_cost_calculation(self, client):
        """recommended_cost should equal recommended_quantity * unit_cost."""
        response = client.get("/api/restock/recommendations")
        data = response.json()

        for item in data:
            expected_cost = round(item["recommended_quantity"] * item["unit_cost"], 2)
            assert abs(item["recommended_cost"] - expected_cost) < 0.01

    def test_recommendations_exclude_legacy_forecast_skus(self, client):
        """Demand forecast SKUs with no matching inventory record (legacy data)
        must never appear, since there's no real cost/stock to base a recommendation on."""
        response = client.get("/api/restock/recommendations")
        data = response.json()

        inventory_response = client.get("/api/inventory")
        valid_skus = {item["sku"] for item in inventory_response.json()}

        for item in data:
            assert item["sku"] in valid_skus

    def test_recommendations_sorted_urgent_first(self, client):
        """Urgent items (understocked + rising demand) must sort ahead of non-urgent ones."""
        response = client.get("/api/restock/recommendations")
        data = response.json()

        seen_non_urgent = False
        for item in data:
            if not item["urgent"]:
                seen_non_urgent = True
            else:
                assert not seen_non_urgent, "An urgent item appeared after a non-urgent item"

    def test_recommendations_sorted_by_gap_within_tier(self, client):
        """Within the same urgency tier, items sort by biggest demand gap first."""
        response = client.get("/api/restock/recommendations")
        data = response.json()

        for previous, current in zip(data, data[1:]):
            if previous["urgent"] == current["urgent"]:
                assert previous["recommended_quantity"] >= current["recommended_quantity"]

    def test_default_selected_none_at_zero_budget(self, client):
        """With no budget, nothing should be marked as the default selection."""
        response = client.get("/api/restock/recommendations?budget=0")
        data = response.json()

        assert all(item["default_selected"] is False for item in data)

    def test_default_selected_all_at_large_budget(self, client):
        """A very large budget should default-select every recommended item."""
        response = client.get("/api/restock/recommendations?budget=1000000")
        data = response.json()

        assert len(data) > 0
        assert all(item["default_selected"] is True for item in data)

    def test_default_selected_stays_within_budget(self, client):
        """The default selection's total cost should never exceed the given budget."""
        response = client.get("/api/restock/recommendations?budget=50")
        data = response.json()

        selected_total = sum(item["recommended_cost"] for item in data if item["default_selected"])
        assert selected_total <= 50


class TestRestockOrdersEndpoint:
    """Test suite for POST/GET /api/restock/orders."""

    def _get_recommendation(self, client, sku):
        response = client.get("/api/restock/recommendations?budget=1000000")
        for item in response.json():
            if item["sku"] == sku:
                return item
        pytest.fail(f"Expected recommendation for {sku} not found")

    def test_create_restock_order_success(self, client):
        """Test submitting a valid restocking order."""
        rec = self._get_recommendation(client, "SNR-420")
        quantity = rec["recommended_quantity"]
        budget = rec["unit_cost"] * quantity

        response = client.post("/api/restock/orders", json={
            "budget": budget,
            "items": [{"sku": "SNR-420", "quantity": quantity}]
        })
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RESTOCK-")
        assert order["status"] == "Processing"
        assert order["lead_time_days"] == 14
        assert abs(order["total_cost"] - round(rec["unit_cost"] * quantity, 2)) < 0.01
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "SNR-420"
        assert abs(order["items"][0]["subtotal"] - order["total_cost"]) < 0.01

    def test_submitted_order_appears_in_order_list(self, client):
        """A newly submitted order should show up in the submitted orders list."""
        rec = self._get_recommendation(client, "CTL-330")
        quantity = 1

        create_response = client.post("/api/restock/orders", json={
            "budget": rec["unit_cost"] * quantity,
            "items": [{"sku": "CTL-330", "quantity": quantity}]
        })
        assert create_response.status_code == 201
        created_order_number = create_response.json()["order_number"]

        list_response = client.get("/api/restock/orders")
        assert list_response.status_code == 200

        order_numbers = [o["order_number"] for o in list_response.json()]
        assert created_order_number in order_numbers
        # Most recently submitted order should be first
        assert order_numbers[0] == created_order_number

    def test_create_restock_order_exceeds_budget(self, client):
        """An order whose cost exceeds the stated budget should be rejected."""
        response = client.post("/api/restock/orders", json={
            "budget": 1.0,
            "items": [{"sku": "SNR-420", "quantity": 100}]
        })
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()

    def test_create_restock_order_unknown_sku(self, client):
        """An order referencing a SKU not in inventory should 404."""
        response = client.post("/api/restock/orders", json={
            "budget": 1000,
            "items": [{"sku": "NOT-A-REAL-SKU", "quantity": 1}]
        })
        assert response.status_code == 404

    def test_create_restock_order_nonpositive_quantity(self, client):
        """An order line with zero or negative quantity should be rejected."""
        response = client.post("/api/restock/orders", json={
            "budget": 1000,
            "items": [{"sku": "SNR-420", "quantity": 0}]
        })
        assert response.status_code == 400

    def test_create_restock_order_empty_items(self, client):
        """An order with no items should be rejected."""
        response = client.post("/api/restock/orders", json={
            "budget": 1000,
            "items": []
        })
        assert response.status_code == 400
