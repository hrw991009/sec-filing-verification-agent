"""Contract tests for public-only DNS pinning and peer verification."""

# Test doubles implement the httpcore2 protocol, whose parameter is named ``timeout``.
# ruff: noqa: ASYNC109

import ssl
from collections.abc import Iterable

import httpcore2
import httpx2
import pytest

from industry_platform.adapters.public_egress import (
    PinnedPublicNetworkBackend,
    PublicEgressTransport,
    create_public_egress_http_client,
)

PUBLIC_IP = "93.184.216.34"
SECOND_PUBLIC_IP = "93.184.216.35"


class StaticResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        self.calls.append((hostname, port))
        return self.addresses


class RecordingStream(httpcore2.AsyncNetworkStream):
    def __init__(self, peer: str, response_bytes: bytes = b"") -> None:
        self.peer = peer
        self.response_bytes = response_bytes
        self.closed = False
        self.tls_server_names: list[str | None] = []
        self.writes: list[bytes] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        response = self.response_bytes[:max_bytes]
        self.response_bytes = self.response_bytes[max_bytes:]
        return response

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        del ssl_context, timeout
        self.tls_server_names.append(server_hostname)
        return self

    def get_extra_info(self, info: str) -> object:
        return (self.peer, 443) if info == "server_addr" else None


class RecordingNetworkBackend(httpcore2.AsyncNetworkBackend):
    def __init__(self, peer: str, response_bytes: bytes = b"") -> None:
        self.stream = RecordingStream(peer, response_bytes)
        self.connections: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore2.SOCKET_OPTION] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        del timeout, local_address, socket_options
        self.connections.append((host, port))
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore2.SOCKET_OPTION] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("public egress must not use Unix sockets")

    async def sleep(self, seconds: float) -> None:
        del seconds


@pytest.mark.asyncio
async def test_public_dns_is_sorted_pinned_and_verified_before_returning_stream() -> None:
    resolver = StaticResolver(SECOND_PUBLIC_IP, PUBLIC_IP)
    network = RecordingNetworkBackend(PUBLIC_IP)
    backend = PinnedPublicNetworkBackend(
        resolver=resolver,
        network_backend=network,
    )

    stream = await backend.connect_tcp("API.Example.COM.", 443)

    assert stream is network.stream
    assert resolver.calls == [("api.example.com", 443)]
    assert network.connections == [(PUBLIC_IP, 443)]
    assert network.stream.closed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.0.0.8",),
        ("169.254.169.254",),
        ("100.100.100.200",),
        ("::1",),
        ("fd00::1",),
        ("::ffff:8.8.8.8",),
        (PUBLIC_IP, "192.168.1.10"),
    ],
)
async def test_any_non_public_dns_answer_rejects_the_entire_target(
    addresses: tuple[str, ...],
) -> None:
    network = RecordingNetworkBackend(PUBLIC_IP)
    backend = PinnedPublicNetworkBackend(
        resolver=StaticResolver(*addresses),
        network_backend=network,
    )

    with pytest.raises(httpcore2.ConnectError, match="Controlled public HTTP transport failed"):
        await backend.connect_tcp("api.example.com", 443)

    assert network.connections == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostname",
    ["localhost", "metadata.google.internal", "service.local", "intranet"],
)
async def test_internal_hostnames_are_rejected_before_dns(hostname: str) -> None:
    resolver = StaticResolver(PUBLIC_IP)
    network = RecordingNetworkBackend(PUBLIC_IP)
    backend = PinnedPublicNetworkBackend(resolver=resolver, network_backend=network)

    with pytest.raises(httpcore2.ConnectError):
        await backend.connect_tcp(hostname, 443)

    assert resolver.calls == []
    assert network.connections == []


@pytest.mark.asyncio
async def test_peer_mismatch_closes_the_socket_before_http_can_use_it() -> None:
    network = RecordingNetworkBackend(SECOND_PUBLIC_IP)
    backend = PinnedPublicNetworkBackend(
        resolver=StaticResolver(PUBLIC_IP),
        network_backend=network,
    )

    with pytest.raises(httpcore2.ConnectError):
        await backend.connect_tcp("api.example.com", 443)

    assert network.stream.closed is True


@pytest.mark.asyncio
async def test_transport_rejects_non_https_before_resolving() -> None:
    resolver = StaticResolver(PUBLIC_IP)
    transport = PublicEgressTransport(
        resolver=resolver,
        network_backend=RecordingNetworkBackend(PUBLIC_IP),
    )
    client = httpx2.AsyncClient(transport=transport, trust_env=False)
    try:
        with pytest.raises(httpx2.UnsupportedProtocol):
            await client.get("http://api.example.com/v1")
    finally:
        await client.aclose()

    assert resolver.calls == []


@pytest.mark.asyncio
async def test_https_keeps_the_dns_name_for_tls_and_host_while_connecting_to_the_pin() -> None:
    resolver = StaticResolver(PUBLIC_IP)
    network = RecordingNetworkBackend(
        PUBLIC_IP,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
    )
    transport = PublicEgressTransport(resolver=resolver, network_backend=network)
    client = httpx2.AsyncClient(transport=transport, trust_env=False)
    try:
        response = await client.get("https://api.example.com/v1/health")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.content == b"{}"
    assert network.connections == [(PUBLIC_IP, 443)]
    assert network.stream.tls_server_names == ["api.example.com"]
    assert b"host: api.example.com" in b"".join(network.stream.writes).lower()


@pytest.mark.asyncio
async def test_production_client_disables_redirects_and_bounds_redirect_count() -> None:
    client = create_public_egress_http_client()
    try:
        assert client.follow_redirects is False
        assert client.max_redirects == 3
        assert client.timeout.connect == 10.0
        assert client.timeout.pool == 5.0
    finally:
        await client.aclose()
