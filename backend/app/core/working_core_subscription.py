"""Bounded, one-shot subscription input for Working Core."""

import asyncio
import binascii
import re
import socket
import ssl
from ipaddress import IPv4Address, IPv4Network
from urllib.parse import urlsplit

import h11
from pydantic import ValidationError

from app.core.uri_parser import _b64_decode
from app.core.working_core import VlessImport, VlessImportError
from app.network_agent.xray_profile import VlessEgress

MAX_PASTED_BYTES = 24 * 1024
MAX_DOWNLOAD_BYTES = 128 * 1024
MAX_ENTRIES = 256
DOWNLOAD_TIMEOUT = 12
HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
PUBLIC_HOST = re.compile(rf"(?:{HOST_LABEL}\.)+[a-z][a-z0-9-]{{0,62}}", re.ASCII)
BASE64 = re.compile(r"[A-Za-z0-9+/_-]+=*")
SPECIAL_NETWORKS = (IPv4Network("192.0.0.0/24"), IPv4Network("192.88.99.0/24"))


class SubscriptionError(ValueError):
    """Safe operator-facing error; never include the source or remote response."""


def subscription_lines(content: str) -> list[tuple[int, str]]:
    text = content.lstrip("\ufeff").strip()
    if BASE64.fullmatch(text):
        try:
            text = _b64_decode(text).lstrip("\ufeff")
        except (ValueError, binascii.Error):
            raise SubscriptionError("Invalid Base64 subscription") from None
    lines = [
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not 1 <= len(lines) <= MAX_ENTRIES:
        raise SubscriptionError("Expected between 1 and 256 subscription entries")
    return lines


def parse_exit(uri: str, dial_ips: dict) -> VlessEgress:
    if not uri.lower().startswith("vless://"):
        raise SubscriptionError("unsupported_protocol")
    try:
        host = urlsplit(uri).hostname
        vless = VlessImport(uri=uri, dial_address=dial_ips.get(host)).vless()
        if getattr(vless.address, "scope_id", None) is not None:
            raise ValueError
        return vless
    except VlessImportError as exc:
        raise SubscriptionError(exc.status) from None
    except (ValidationError, ValueError):
        raise SubscriptionError("incompatible_vless") from None


def _request_target(url: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (not url.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in url)
                or "\\" in url or "#" in url or parsed.scheme != "https"
                or host is None or len(host) > 253 or not PUBLIC_HOST.fullmatch(host)
                or parsed.netloc.lower() not in {host, f"{host}:443"}
                or parsed.path.startswith("//")):
            raise ValueError
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        return host, target
    except ValueError:
        raise SubscriptionError("Use a public HTTPS hostname on port 443, without credentials or fragments") from None


async def _resolve_public_ipv4(host: str) -> str:
    records = await asyncio.get_running_loop().getaddrinfo(
        host + ".", 443, family=socket.AF_INET, type=socket.SOCK_STREAM,
    )
    addresses = [IPv4Address(record[4][0]) for record in records]
    if not addresses or any(
        not address.is_global or address.is_multicast or address.is_reserved
        or any(address in network for network in SPECIAL_NETWORKS)
        for address in addresses
    ):
        raise SubscriptionError("Subscription hostname must resolve only to public IPv4 addresses")
    return str(addresses[0])


def _check_response(response: h11.Response) -> None:
    if response.status_code != 200:
        raise SubscriptionError("Subscription server must return HTTP 200 without a redirect")
    for key, value in response.headers:
        if key == b"content-encoding" and value.lower() != b"identity":
            raise SubscriptionError("Compressed HTTPS subscription responses are not supported")
        if key == b"content-length" and int(value) > MAX_DOWNLOAD_BYTES:
            raise SubscriptionError("Subscription response exceeds 128 KiB")


async def _read_response(reader: asyncio.StreamReader, connection: h11.Connection) -> str:
    body = bytearray()
    while True:
        event = connection.next_event()
        if event is h11.NEED_DATA:
            connection.receive_data(await reader.read(8192))
        elif isinstance(event, h11.Response):
            _check_response(event)
        elif isinstance(event, h11.Data):
            if len(body) + len(event.data) > MAX_DOWNLOAD_BYTES:
                raise SubscriptionError("Subscription response exceeds 128 KiB")
            body.extend(event.data)
        elif isinstance(event, h11.EndOfMessage):
            return body.decode("utf-8-sig")
        else:
            raise SubscriptionError("Unexpected HTTPS subscription response")


async def fetch_subscription(url: str) -> str:
    host, target = _request_target(url)
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT):
            address = await _resolve_public_ipv4(host)
            # Pin the checked address and retain the original name for TLS and Host.
            # This connection has no ambient proxy, redirect handler, or URL logging.
            reader, writer = await asyncio.open_connection(
                address, 443, family=socket.AF_INET, flags=socket.AI_NUMERICHOST,
                ssl=ssl.create_default_context(), server_hostname=host,
                ssl_shutdown_timeout=1,
            )
            try:
                connection = h11.Connection(h11.CLIENT)
                writer.write(connection.send(h11.Request(method="GET", target=target, headers=[
                    ("Host", host), ("Accept-Encoding", "identity"),
                    ("User-Agent", "TEMPEST"), ("Connection", "close"),
                ])))
                writer.write(connection.send(h11.EndOfMessage()))
                await writer.drain()
                return await _read_response(reader, connection)
            finally:
                writer.close()
    except SubscriptionError:
        raise
    except (OSError, TimeoutError, ValueError, h11.ProtocolError):
        raise SubscriptionError("Could not download a UTF-8 subscription over HTTPS") from None
