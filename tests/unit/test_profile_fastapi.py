from unittest.mock import AsyncMock, Mock, call

import pytest

from app.core.config import Settings
from scripts import profile_fastapi


@pytest.mark.asyncio
async def test_pool_experiment_restores_compose_settings_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = AsyncMock(return_value={})
    configure = Mock()
    monkeypatch.setattr(profile_fastapi, "connection_budget", budget)
    monkeypatch.setattr(profile_fastapi, "configure_api", configure)
    monkeypatch.setattr(
        profile_fastapi, "batch", AsyncMock(side_effect=RuntimeError("sample failed"))
    )

    with pytest.raises(RuntimeError, match="sample failed"):
        await profile_fastapi.pools("http://localhost", Settings(), 4, 4)

    assert configure.call_args_list == [
        call(profile_fastapi.POOL_CONFIGS["control"]),
        call(None),
    ]
