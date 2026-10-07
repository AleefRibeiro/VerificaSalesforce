"""Bounded HTTPS transports. Public HTML uses checked, pinned DNS and verified TLS."""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from .resolution import InvalidTarget, normalize_domain


class FetchError(ValueError):
    pass


def public_addresses(host: str) -> list[str]:
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    return validate_addresses(addresses)


def validate_addresses(addresses: list[str]) -> list[str]:
    if not addresses:
        raise FetchError("dns_empty")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        effective = parsed.ipv4_mapped if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped else parsed
        transition_ranges = ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
        transition = isinstance(parsed, ipaddress.IPv6Address) and any(parsed in ipaddress.ip_network(net) for net in transition_ranges)
        if not effective.is_global or effective.is_multicast or transition:
            raise FetchError("non_public_address")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        # Connect to the validated numeric address; retain original SNI and hostname verification.
        deadline = time.monotonic() + self.timeout
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            raw.settimeout(max(.01, deadline - time.monotonic()))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self.sock.do_handshake()
        except Exception:
            raw.close()
            raise


@dataclass(frozen=True)
class Page:
    url: str
    html: str


class PublicHTMLFetcher:
    def __init__(self, resolver: Callable = public_addresses, connection_factory: Callable = PinnedHTTPSConnection,
                 max_bytes: int = 512 * 1024, total_seconds: float = 10):
        self.resolver, self.connection_factory = resolver, connection_factory
        self.max_bytes, self.total_seconds = max_bytes, total_seconds

    async def fetch(self, domain: str) -> Page:
        normalized = normalize_domain(domain)
        try:
            return await asyncio.wait_for(self._fetch(normalized), self.total_seconds + .5)
        except (TimeoutError, OSError, ssl.SSLError, http.client.HTTPException, InvalidTarget) as exc:
            raise FetchError("public_fetch_failed") from exc

    async def _fetch(self, domain: str) -> Page:
        deadline, url = time.monotonic() + self.total_seconds, "https://" + domain + "/"
        for redirect in range(3):
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or parsed.hostname not in {domain, "www." + domain}):
                raise FetchError("redirect_outside_explicit_domain")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("deadline")
            addresses = validate_addresses(await asyncio.wait_for(asyncio.to_thread(self.resolver, parsed.hostname), min(3, remaining)))
            status, headers, data = await asyncio.to_thread(self._read, parsed, addresses[0], deadline)
            if status in {301, 302, 303, 307, 308}:
                if redirect == 2 or not headers.get("location"):
                    raise FetchError("redirect_limit")
                url = urljoin(url, headers["location"])
                continue
            if status != 200:
                raise FetchError("public_http_status")
            if "text/html" not in headers.get("content-type", "").lower():
                raise FetchError("not_html")
            return Page(url=url, html=data.decode("utf-8", errors="replace"))
        raise FetchError("redirect_limit")

    def _read(self, parsed, address: str, deadline: float):
        conn = self.connection_factory(parsed.hostname, address, max(.01, deadline - time.monotonic()))
        read_socket = None

        def abort():
            # A wall-clock stop also interrupts slow headers/body in the worker thread.
            sock = read_socket or conn.sock
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            conn.close()

        timer = threading.Timer(max(.01, deadline - time.monotonic()), abort)
        timer.daemon = True
        timer.start()
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn.request("GET", path, headers={"User-Agent": "AveronResearch/2.0", "Accept": "text/html", "Accept-Encoding": "identity"})
            read_socket = conn.sock
            if read_socket:
                read_socket.settimeout(max(.01, deadline - time.monotonic()))
            response = conn.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if response.status in {301, 302, 303, 307, 308}:
                return response.status, headers, b""
            if headers.get("content-encoding", "identity") != "identity":
                raise FetchError("compressed_response_rejected")
            if int(headers.get("content-length", "0")) > self.max_bytes:
                raise FetchError("response_too_large")
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("deadline")
                if read_socket:
                    read_socket.settimeout(remaining)
                chunk = response.read1(min(65536, self.max_bytes + 1 - len(body)))
                body.extend(chunk)
                if len(body) > self.max_bytes:
                    raise FetchError("response_too_large")
                if not chunk:
                    return response.status, headers, bytes(body)
        finally:
            timer.cancel()
            conn.close()


class ProviderJSONTransport:
    """Fixed vendor hosts only; no redirects, retries, proxy env or unbounded responses."""
    ALLOWED = {"https://api.theirstack.com/v1/jobs/search", "https://api.apollo.io/api/v1/organizations/enrich"}

    async def request(self, method: str, url: str, *, headers: dict, params: dict | None = None,
                      json_body: dict | None = None) -> dict:
        if url not in self.ALLOWED:
            raise FetchError("vendor_endpoint_not_allowed")
        try:
            async with httpx.AsyncClient(timeout=10, verify=True, trust_env=False, follow_redirects=False) as client:
                async with client.stream(method, url, headers=headers, params=params, json=json_body) as response:
                    if response.status_code != 200:
                        raise FetchError("vendor_http_" + str(response.status_code))
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise FetchError("vendor_response_too_large")
                    value = json.loads(body)
                    if not isinstance(value, dict):
                        raise FetchError("vendor_invalid_response")
                    return value
        except (httpx.HTTPError, ValueError) as exc:
            raise FetchError("vendor_request_failed") from exc
