from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from re import compile as compile_pattern

from fastapi import HTTPException, Request, status

LOCAL_AUTHORITY = compile_pattern(
    r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?"
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
    hosts = request.headers.getlist("host")
    if (
        request.client is None
        or len(hosts) != 1
        or any(
            header in {"forwarded", "x-real-ip"} or header.startswith("x-forwarded-")
            for header in request.headers
        )
    ):
        raise hidden
    authority = LOCAL_AUTHORITY.fullmatch(hosts[0].lower())
    if authority is None or (
        authority[1] is not None and not 1 <= int(authority[1]) <= 65535
    ):
        raise hidden
    try:
        peer = ip_address(request.client.host)
    except ValueError:
        raise hidden from None
    if isinstance(peer, IPv6Address) and peer.ipv4_mapped is not None:
        peer = peer.ipv4_mapped
    if not (peer.is_loopback or peer == request.app.state.lab_gateway):
        raise hidden
