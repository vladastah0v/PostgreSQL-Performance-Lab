import httpx
import pytest

from app.core.config import Settings
from app.core.database import get_db_session
from app.main import create_app

PATH = "/api/v1/baseline/users/search"


@pytest.mark.asyncio
async def test_baseline_routes_are_absent_outside_lab_mode() -> None:
    app = create_app(Settings(lab_mode=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get(PATH)).status_code == 404
        openapi = (await client.get("/openapi.json")).json()
    assert all(not path.startswith("/api/v1/baseline") for path in openapi["paths"])
    assert "/api/v1/optimized/users/search" in openapi["paths"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("peer", "host", "headers"),
    [
        ("198.51.100.2", "localhost", {}),
        ("172.20.0.3", "localhost", {}),
        ("198.51.100.2", "localhost", {"x-forwarded-for": "127.0.0.1"}),
        ("127.0.0.1", "localhost", {"forwarded": "for=198.51.100.2"}),
        ("127.0.0.1", "example.test", {}),
    ],
)
async def test_baseline_rejects_untrusted_requests(
    peer: str, host: str, headers: dict[str, str]
) -> None:
    app = create_app(Settings(lab_mode=True, lab_allow_docker_gateway=False))

    async def unexpected_database():
        pytest.fail("A blocked request reached the database dependency")

    app.dependency_overrides[get_db_session] = unexpected_database
    transport = httpx.ASGITransport(app=app, client=(peer, 9000))
    async with httpx.AsyncClient(
        transport=transport, base_url=f"http://{host}"
    ) as client:
        response = await client.get(PATH, headers=headers)
    assert response.status_code == 404
