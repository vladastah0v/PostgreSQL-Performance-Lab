from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Scenario:
    name: str
    path: str
    parameters: dict[str, str | int] = field(default_factory=dict)
    analytics: bool = False

    def url(self, variant: str) -> str:
        return f"/api/v1/{variant}{self.path}"

    def cache_status(self, variant: str) -> str | None:
        return "BYPASS" if self.analytics and variant == "optimized" else None


DATES = {"date_from": "2025-01-01", "date_to": "2025-12-31"}
SCENARIOS = (
    Scenario(
        "users-search",
        "/users/search",
        {"name": "Alex", "city": "New York", "status": "active", "limit": 20},
    ),
    Scenario("user-orders", "/users/1/orders", {"limit": 20}),
    Scenario(
        "orders",
        "/orders",
        {**DATES, "status": "delivered", "limit": 20, "offset": 100},
    ),
    Scenario("order-details", "/orders/1/details"),
    Scenario("sales", "/analytics/sales", {**DATES, "period": "month"}, analytics=True),
    Scenario(
        "top-products",
        "/analytics/top-products",
        {**DATES, "limit": 20},
        analytics=True,
    ),
    Scenario("customers", "/analytics/customers", DATES, analytics=True),
    Scenario(
        "events", "/events", {"user_id": 1, "event_type": "product_view", "limit": 20}
    ),
)


def select_scenarios(name: str) -> tuple[Scenario, ...]:
    if name == "all":
        return SCENARIOS
    scenarios = tuple(item for item in SCENARIOS if item.name == name)
    if not scenarios:
        raise ValueError(f"Unknown HTTP scenario {name}")
    return scenarios


def validate_loopback(url: str) -> None:
    address = urlsplit(url)
    if (
        address.scheme != "http"
        or address.hostname not in {"localhost", "127.0.0.1", "::1"}
        or address.username is not None
        or address.path not in ("", "/")
        or address.query
        or address.fragment
    ):
        raise ValueError("The target must be a local HTTP origin")


@dataclass(frozen=True)
class LoadConfig:
    host: str
    scenario: str
    variant: str
    users: int
    spawn_rate: float
    warmup: float
    duration: float
    max_requests: int
    request_timeout: float

    def __post_init__(self) -> None:
        validate_loopback(self.host)
        select_scenarios(self.scenario)
        if self.variant not in {"baseline", "optimized"}:
            raise ValueError("Use a known API variant")
        if (
            not 1 <= self.users <= 200
            or not 0.1 <= self.spawn_rate <= 200
            or self.users / self.spawn_rate > 60
        ):
            raise ValueError(
                "Use at most 200 users and a ramp completed within 60 seconds"
            )
        if not 0 <= self.warmup <= 30 or not 1 <= self.duration <= 300:
            raise ValueError(
                "Use a warmup of 0–30 seconds and a duration of 1–300 seconds"
            )
        if (
            not 1 <= self.max_requests <= 20_000
            or not 0.2 <= self.request_timeout <= 30
        ):
            raise ValueError(
                "Use at most 20,000 measured requests and a timeout of 0.2–30 seconds"
            )
