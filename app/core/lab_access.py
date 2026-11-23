from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path

from fastapi import HTTPException, Request, status

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PROXY_HEADERS = frozenset(
    {
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)


def docker_gateway() -> IPv4Address | None:
    # Behind Docker Desktop's loopback port forward, Uvicorn can see the bridge gateway.
    try:
        routes = Path("/proc/net/route").read_text().splitlines()[1:]
        for line in routes:
            _, destination, gateway, flags, *_ = line.split()
            if destination == "00000000" and int(flags, 16) & 0x2:
                return IPv4Address(bytes.fromhex(gateway)[::-1])
    except (OSError, ValueError):
        return None
    return None


async def require_local_lab_client(request: Request) -> None:
    hidden = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if request.client is None or PROXY_HEADERS.intersection(request.headers):
        raise hidden
    try:
        hostname = request.url.hostname
        peer = ip_address(request.client.host)
    except ValueError:
        raise hidden from None
    if isinstance(peer, IPv6Address) and peer.ipv4_mapped is not None:
        peer = peer.ipv4_mapped
    if hostname not in LOCAL_HOSTS or not (
        peer.is_loopback or peer == request.app.state.lab_gateway
    ):
        raise hidden
