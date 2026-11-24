import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families

from app.core.config import Settings
from app.core.database import get_db_session
from app.main import create_app


@pytest.mark.asyncio
async def test_http_metrics_group_path_parameters_and_unknown_paths() -> None:
    app = create_app(Settings(api_metrics_enabled=True))
    app.dependency_overrides[get_db_session] = lambda: None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        for order_id in ("raw-order-123", "raw-order-456"):
            response = await client.get(f"/api/v1/optimized/orders/{order_id}/details")
            assert response.status_code == 422
        assert (await client.get("/api/v1/raw-path-789")).status_code == 404
        assert (await client.get("/health/live")).status_code == 200
        exported = await client.get("/metrics")

    requests = {
        (sample.labels["route"], sample.labels["status"]): sample.value
        for family in text_string_to_metric_families(exported.text)
        for sample in family.samples
        if sample.name == "lab_http_requests_total"
    }
    assert requests == {
        ("/api/v1/optimized/orders/{order_id}/details", "422"): 2,
        ("<unmatched>", "404"): 1,
    }
    assert "raw-" not in exported.text


@pytest.mark.asyncio
async def test_disabling_instrumentation_keeps_the_cache_metrics() -> None:
    app = create_app(Settings(api_metrics_enabled=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/v1/does-not-exist")).status_code == 404
        exported = await client.get("/metrics")

    assert "analytics_cache_requests_total" in exported.text
    assert "lab_http_requests_total" not in exported.text
