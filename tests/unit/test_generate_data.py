import json
from dataclasses import replace
from datetime import UTC

import pytest

from app.core.config import Settings
from scripts.data_rows import (
    DatasetSpec,
    event_rows,
    order_rows,
    product_rows,
    user_rows,
)
from scripts.generate_data import (
    DatasetError,
    validate_clear_confirmation,
    verify_counts,
)


@pytest.mark.parametrize("rows", [user_rows, product_rows, order_rows, event_rows])
def test_row_streams_are_deterministic_for_a_seed(rows) -> None:
    spec = DatasetSpec(50, 80, 100, 42)

    original = list(rows(spec))

    assert original == list(rows(spec))
    assert original != list(rows(replace(spec, seed=43)))


def test_generated_orders_reconcile_with_items_payments_and_history() -> None:
    spec = DatasetSpec(100, 80, 100, 42)
    product_prices = {row[0]: row[4] for row in product_rows(spec)}

    for record in order_rows(spec):
        order_id, user_id, status, total, created_at, updated_at = record.order
        assert 1 <= user_id <= spec.users
        assert created_at.tzinfo is UTC
        assert 2 <= len(record.items) <= 4
        assert total == sum(item[3] * item[4] for item in record.items)
        for _, owning_order, product_id, quantity, price in record.items:
            assert owning_order == order_id
            assert quantity > 0
            assert price == product_prices[product_id]

        _, paid_order, payment_status, amount, paid_at, payment_created_at = (
            record.payment
        )
        assert paid_order == order_id
        assert amount == total
        assert payment_created_at == created_at
        assert (paid_at is not None) == (payment_status in {"paid", "refunded"})
        if paid_at is not None:
            assert created_at < paid_at <= updated_at

        previous_status = None
        for _, owning_order, old_status, new_status, changed_at in record.history:
            assert owning_order == order_id
            assert old_status == previous_status
            assert old_status != new_status
            assert created_at <= changed_at <= updated_at
            previous_status = new_status
        assert previous_status == status


@pytest.mark.parametrize("order_count", [0, 1, 2, 20, 21, 22])
def test_history_has_two_entries_per_order_on_average(order_count: int) -> None:
    spec = DatasetSpec(10, order_count, 0, 42)
    assert sum(len(record.history) for record in order_rows(spec)) == 2 * order_count


def test_event_product_references_and_payload_are_valid() -> None:
    spec = DatasetSpec(1, 0, 200, 42)

    for event_id, user_id, event_type, raw_payload, created_at in event_rows(spec):
        assert 1 <= event_id <= spec.events
        assert user_id == 1
        assert created_at.tzinfo is UTC
        payload = json.loads(raw_payload)
        assert {"source", "device"} <= payload.keys()
        if event_type in {"product_view", "add_to_cart"}:
            assert 1 <= payload["product_id"] <= spec.products


def test_row_count_verification_rejects_out_of_range_items() -> None:
    spec = DatasetSpec(10, 2, 0, 42)
    valid_counts = {**spec.fixed_counts, "order_items": 5}
    verify_counts(spec, valid_counts, valid_counts)

    wrong_counts = {**valid_counts, "order_items": 9}
    with pytest.raises(DatasetError, match="differ from generated counts"):
        verify_counts(spec, valid_counts, wrong_counts)
    with pytest.raises(DatasetError, match="outside the expected range"):
        verify_counts(spec, wrong_counts, wrong_counts)


@pytest.mark.parametrize("confirmation", [None, "other_db", "PERFORMANCE_LAB"])
def test_clear_requires_exact_confirmation(confirmation: str | None) -> None:
    settings = Settings(postgres_host="127.0.0.1", postgres_db="performance_lab")
    with pytest.raises(DatasetError, match="exact database name"):
        validate_clear_confirmation(settings, confirmation)


def test_clear_rejects_remote_database() -> None:
    settings = Settings(postgres_host="example.test", postgres_db="performance_lab")
    with pytest.raises(DatasetError, match="local or Compose"):
        validate_clear_confirmation(settings, "performance_lab")
