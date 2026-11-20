"""Subscription transport tests with simulated DNS and TLS streams."""

import asyncio
import base64
import socket
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import working_core_subscription as subscription


@pytest.mark.parametrize("url", [
    "http://example.org/list", "https://example.org:444/list", "https://a@example.org",
    "https://example.org\\@127.0.0.1", "https://example.org/#fragment",
    "https://localhost/list", "https://127.0.0.1/list", "https://[::1]/list",
    "https://example.org/\nAuthorization: secret",
])
@pytest.mark.asyncio
async def test_invalid_urls_fail_before_dns(monkeypatch, url):
    dns = AsyncMock(side_effect=AssertionError("unexpected DNS"))
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    with pytest.raises(subscription.SubscriptionError, match="public HTTPS"):
        await subscription.fetch_subscription(url)
    dns.assert_not_awaited()


@pytest.mark.parametrize("blocked", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "224.0.0.1", "192.0.0.8", "0.0.0.0",
])
@pytest.mark.asyncio
async def test_mixed_public_and_nonpublic_dns_answers_fail_before_tls(monkeypatch, blocked):
    dns = AsyncMock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        for address in ["8.8.8.8", blocked]
    ])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    connect = AsyncMock(side_effect=AssertionError("unexpected TLS"))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    with pytest.raises(subscription.SubscriptionError, match="public IPv4"):
        await subscription.fetch_subscription("https://example.org/sub")
    dns.assert_awaited_once_with("example.org.", 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    connect.assert_not_awaited()


@pytest.mark.parametrize(("wire", "expected"), [
    (b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ntext", None),
    (b"HTTP/1.1 302 Found\r\nLocation: https://10.0.0.1/url-secret\r\n\r\n", "without a redirect"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 131073\r\n\r\n", "exceeds"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n20001\r\n" + b"x" * 131073 + b"\r\n0\r\n\r\n", "exceeds"),
    (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n", "Compressed"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\ntext", "Could not download"),
])
@pytest.mark.asyncio
async def test_fetch_pins_ip_preserves_tls_and_bounds_response(monkeypatch, caplog, wire, expected):
    resolve = AsyncMock(return_value="8.8.8.8")
    monkeypatch.setattr(subscription, "_resolve_public_ipv4", resolve)
    reader = asyncio.StreamReader()
    reader.feed_data(wire)
    reader.feed_eof()
    writer = Mock(drain=AsyncMock())
    connect = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    caplog.set_level("DEBUG")
    url = "https://example.org/list?token=url-secret"
    if expected is None:
        assert await subscription.fetch_subscription(url) == "text"
    else:
        with pytest.raises(subscription.SubscriptionError, match=expected) as exc:
            await subscription.fetch_subscription(url)
        assert "url-secret" not in str(exc.value)
    assert "url-secret" not in caplog.text
    resolve.assert_awaited_once_with("example.org")
    assert connect.await_count == 1
    assert connect.call_args.args == ("8.8.8.8", 443)
    options = connect.call_args.kwargs
    assert options["flags"] == socket.AI_NUMERICHOST
    assert options["server_hostname"] == "example.org"
    assert options["ssl"].check_hostname
    request = writer.write.call_args_list[0].args[0]
    assert b"GET /list?token=url-secret HTTP/1.1\r\n" in request
    assert b"Host: example.org\r\n" in request
    writer.close.assert_called_once()


def test_subscription_entries_are_bounded_and_base64_preserves_line_numbers():
    encoded = base64.urlsafe_b64encode(b"# comment\n\nvless://entry\nss://entry").decode().rstrip("=")
    assert subscription.subscription_lines(encoded) == [(3, "vless://entry"), (4, "ss://entry")]
    with pytest.raises(subscription.SubscriptionError, match="256"):
        subscription.subscription_lines("vless://entry\n" * 257)


@pytest.mark.asyncio
async def test_download_deadline_covers_dns(monkeypatch):
    never = asyncio.Event()

    async def slow_dns(_host):
        await never.wait()

    monkeypatch.setattr(subscription, "DOWNLOAD_TIMEOUT", 0.01)
    monkeypatch.setattr(subscription, "_resolve_public_ipv4", slow_dns)
    with pytest.raises(subscription.SubscriptionError, match="Could not download"):
        await subscription.fetch_subscription("https://example.org/sub")
