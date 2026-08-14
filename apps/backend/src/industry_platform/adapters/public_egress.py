"""Public-only HTTP transport with DNS pinning and pre-request peer checks."""

# The httpcore2 network backend protocol fixes the parameter name as ``timeout``.
# ruff: noqa: ASYNC109

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from typing import NoReturn, Protocol

import httpcore2
import httpx2
import idna

_INTERNAL_HOSTS = frozenset(
    {
        "instance-data",
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
    }
)
_INTERNAL_SUFFIXES = (".home", ".internal", ".lan", ".local", ".localhost")
_TRANSPORT_ERROR = "Controlled public HTTP transport failed"


class PublicHostResolver(Protocol):
    """Resolve every address for one normalized DNS hostname."""

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...


class SystemPublicHostResolver:
    """Use the event loop resolver without blocking the Worker."""

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        addresses = {
            socket_address[0]
            for _family, _type, _protocol, _canonical_name, socket_address in records
            if socket_address and isinstance(socket_address[0], str)
        }
        return tuple(sorted(addresses))


class PinnedPublicNetworkBackend(httpcore2.AsyncNetworkBackend):
    """Resolve, validate, pin, and verify a public TCP peer before HTTP writes."""

    def __init__(
        self,
        *,
        resolver: PublicHostResolver | None = None,
        network_backend: httpcore2.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolver = resolver or SystemPublicHostResolver()
        self._network_backend = network_backend or httpcore2.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore2.SOCKET_OPTION] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        try:
            hostname = _normalize_public_hostname(host)
            resolved = await self._resolver.resolve(hostname, port)
            addresses = _require_only_public_addresses(resolved)
        except (OSError, UnicodeError, ValueError, idna.IDNAError):
            raise httpcore2.ConnectError(_TRANSPORT_ERROR) from None

        selected = addresses[0]
        stream = await self._network_backend.connect_tcp(
            str(selected),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        if not _peer_matches(stream, selected):
            await stream.aclose()
            raise httpcore2.ConnectError(_TRANSPORT_ERROR)
        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore2.SOCKET_OPTION] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        del path, timeout, socket_options
        raise httpcore2.UnsupportedProtocol(_TRANSPORT_ERROR)

    async def sleep(self, seconds: float) -> None:
        await self._network_backend.sleep(seconds)


class _HttpcoreResponseStream(httpx2.AsyncByteStream):
    def __init__(
        self,
        stream: AsyncIterable[bytes],
        request: httpx2.Request,
    ) -> None:
        self._stream = stream
        self._request = request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for part in self._stream:
                yield part
        except httpcore2.NetworkError as error:
            _raise_httpx_error(error, request=self._request)
        except httpcore2.ProtocolError as error:
            _raise_httpx_error(error, request=self._request)
        except httpcore2.TimeoutException as error:
            _raise_httpx_error(error, request=self._request)

    async def aclose(self) -> None:
        close = getattr(self._stream, "aclose", None)
        if close is not None:
            await close()


class PublicEgressTransport(httpx2.AsyncBaseTransport):
    """Adapt the pinned public network backend to the httpx2 transport contract."""

    def __init__(
        self,
        *,
        resolver: PublicHostResolver | None = None,
        network_backend: httpcore2.AsyncNetworkBackend | None = None,
        max_connections: int = 8,
        max_keepalive_connections: int = 4,
    ) -> None:
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=15.0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=PinnedPublicNetworkBackend(
                resolver=resolver,
                network_backend=network_backend,
            ),
        )

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.scheme != "https" or request.url.port not in {None, 443}:
            raise httpx2.UnsupportedProtocol(_TRANSPORT_ERROR, request=request)
        if not isinstance(request.stream, httpx2.AsyncByteStream):
            raise httpx2.LocalProtocolError(_TRANSPORT_ERROR, request=request)

        core_request = httpcore2.Request(
            method=request.method,
            url=httpcore2.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self._pool.handle_async_request(core_request)
        except httpcore2.TimeoutException as error:
            _raise_httpx_error(error, request=request)
        except httpcore2.NetworkError as error:
            _raise_httpx_error(error, request=request)
        except httpcore2.ProtocolError as error:
            _raise_httpx_error(error, request=request)

        if not isinstance(response.stream, AsyncIterable):
            raise httpx2.RemoteProtocolError(_TRANSPORT_ERROR, request=request)
        return httpx2.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_HttpcoreResponseStream(response.stream, request),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


def create_public_egress_http_client() -> httpx2.AsyncClient:
    """Create the shared Provider/Web client with proxies and redirects disabled."""

    return httpx2.AsyncClient(
        transport=PublicEgressTransport(),
        timeout=httpx2.Timeout(30.0, connect=10.0, pool=5.0),
        follow_redirects=False,
        max_redirects=3,
        trust_env=False,
    )


def _normalize_public_hostname(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("Hostname is invalid")
    hostname = (
        idna.encode(
            value.removesuffix("."),
            uts46=True,
            std3_rules=True,
        )
        .decode("ascii")
        .casefold()
    )
    if "." not in hostname or hostname in _INTERNAL_HOSTS or hostname.endswith(_INTERNAL_SUFFIXES):
        raise ValueError("Hostname is not public")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    raise ValueError("IP literals are not accepted as hostnames")


def _require_only_public_addresses(
    values: tuple[str, ...],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    if not values:
        raise ValueError("DNS returned no addresses")

    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for value in values:
        address = ipaddress.ip_address(value.partition("%")[0])
        if not address.is_global or (
            isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
        ):
            raise ValueError("DNS returned a non-public address")
        addresses.add(address)
    return tuple(sorted(addresses, key=lambda item: (item.version, item.packed)))


def _peer_matches(
    stream: httpcore2.AsyncNetworkStream,
    selected: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    peer = stream.get_extra_info("server_addr")
    if not isinstance(peer, tuple) or not peer or not isinstance(peer[0], str):
        return False
    try:
        peer_address = ipaddress.ip_address(peer[0].partition("%")[0])
    except ValueError:
        return False
    return peer_address == selected


def _raise_httpx_error(error: Exception, *, request: httpx2.Request) -> NoReturn:
    mappings: tuple[
        tuple[type[Exception], type[httpx2.TransportError]],
        ...,
    ] = (
        (httpcore2.ConnectTimeout, httpx2.ConnectTimeout),
        (httpcore2.ReadTimeout, httpx2.ReadTimeout),
        (httpcore2.WriteTimeout, httpx2.WriteTimeout),
        (httpcore2.PoolTimeout, httpx2.PoolTimeout),
        (httpcore2.ConnectError, httpx2.ConnectError),
        (httpcore2.ReadError, httpx2.ReadError),
        (httpcore2.WriteError, httpx2.WriteError),
        (httpcore2.UnsupportedProtocol, httpx2.UnsupportedProtocol),
        (httpcore2.LocalProtocolError, httpx2.LocalProtocolError),
        (httpcore2.RemoteProtocolError, httpx2.RemoteProtocolError),
        (httpcore2.ProtocolError, httpx2.ProtocolError),
        (httpcore2.NetworkError, httpx2.NetworkError),
        (httpcore2.TimeoutException, httpx2.TimeoutException),
    )
    for source, target in mappings:
        if isinstance(error, source):
            raise target(_TRANSPORT_ERROR, request=request) from None
    raise httpx2.TransportError(_TRANSPORT_ERROR, request=request) from None
