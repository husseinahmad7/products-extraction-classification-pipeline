"""Bounded HTTP acquisition with DNS-pinned connections and fail-closed robots policy."""

from __future__ import annotations

import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from .contracts import SourceSpec
from .engine import MISSING, pointer_get
from .errors import PipelineError
from .hashing import canonical
from .jsonutil import loads

USER_AGENT = "ProductPipeline/0.1 (+https://github.com/husseinahmad7/products-extraction-classification-pipeline)"


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Transition/NAT64 addresses can route an apparently public IPv6
        # destination onto a different IPv4 trust boundary. Fail closed.
        if address.sixtofour is not None or address.teredo is not None:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            return False
        if address.ipv4_mapped is not None:
            return public_address(str(address.ipv4_mapped))
    return True


def validated_target(url: str, allowed_domains: list[str]):
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise PipelineError("URL_POLICY_DENIED", "invalid URL destination") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise PipelineError("URL_POLICY_DENIED", "HTTP(S) without URL credentials is required")
    if any(
        re.sub(r"[^a-z]", "", key.lower())
        in {
            "key",
            "apikey",
            "token",
            "accesstoken",
            "secret",
            "signature",
            "password",
            "authorization",
        }
        for key, _ in parse_qsl(parsed.query)
    ):
        raise PipelineError("URL_POLICY_DENIED", "secret query parameters are unsupported")
    try:
        host = hostname.encode("idna").decode().lower().rstrip(".")
    except (UnicodeError, ValueError) as exc:
        raise PipelineError("URL_POLICY_DENIED", "invalid destination hostname") from exc
    if host not in allowed_domains:
        raise PipelineError("DOMAIN_DENIED", "host is not explicitly allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise PipelineError("PORT_DENIED", "invalid destination port") from exc
    if port not in {80, 443}:
        raise PipelineError("PORT_DENIED", "only HTTP and HTTPS ports are supported")
    return parsed, host, port


def resolve_public(url: str, allowed_domains: list[str]) -> tuple[str, int, str]:
    _, host, port = validated_target(url, allowed_domains)
    try:
        addresses = sorted(
            {str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        )
    except OSError as exc:
        raise PipelineError("DNS_FAILED", "hostname resolution failed", retryable=True) from exc
    if not addresses or any(not public_address(ip) for ip in addresses):
        raise PipelineError("ADDRESS_DENIED", "non-public destination rejected")
    return host, port, str(addresses[0])


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, secure: bool, timeout: int):
        super().__init__(host, port, timeout=timeout)
        self.address, self.secure = address, secure

    def connect(self) -> None:
        # No second DNS lookup: certificate/SNI still use the original hostname.
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = (
                ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
                if self.secure
                else sock
            )
        except BaseException:
            sock.close()
            raise


@dataclass(frozen=True)
class Response:
    body: bytes
    status: int
    content_type: str
    final_url: str


@dataclass(frozen=True)
class PageCapture:
    url: str
    raw: bytes
    snapshot: bytes
    retained: bool = False
    request_url: str | None = None


def _authorization(source: SourceSpec, url: str) -> str | None:
    if not source.auth_ref:
        return None
    initial = urlsplit(source.url)
    target = urlsplit(url)
    if (target.scheme, target.netloc) != (initial.scheme, initial.netloc):
        return None
    secret = os.environ.get(source.auth_ref[4:])
    if not secret or len(secret) > 4096 or not secret.isascii() or any(c.isspace() for c in secret):
        raise PipelineError("AUTH_UNAVAILABLE", "source credential reference is unavailable")
    return "Bearer " + secret


def fetch(
    url: str,
    source: SourceSpec,
    max_bytes: int | None = None,
    *,
    authorize=None,
    deadline: float | None = None,
) -> Response:
    budget = max_bytes or source.limits.max_bytes
    deadline = deadline or time.monotonic() + source.limits.deadline_seconds
    for _ in range(6):
        if time.monotonic() >= deadline:
            raise PipelineError("LIMIT_REACHED", "acquisition deadline reached")
        if authorize:
            authorize(url)
        host, port, address = resolve_public(url, source.allowed_domains)
        parsed = urlsplit(url)
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        conn = PinnedConnection(
            host, port, address, parsed.scheme == "https", source.limits.request_timeout_seconds
        )
        try:
            conn.request(
                "GET",
                path,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    **({"Authorization": token} if (token := _authorization(source, url)) else {}),
                },
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise PipelineError("REDIRECT_INVALID", "redirect has no destination")
                next_url = urljoin(url, location)
                if parsed.scheme == "https" and urlsplit(next_url).scheme != "https":
                    raise PipelineError("REDIRECT_DENIED", "HTTPS downgrade rejected")
                url = next_url
                continue
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise PipelineError(
                    "CONTENT_ENCODING_UNSUPPORTED", "compressed responses are rejected"
                )
            body = bytearray()
            while len(body) <= budget:
                if time.monotonic() >= deadline:
                    raise PipelineError("LIMIT_REACHED", "acquisition deadline reached")
                if conn.sock is not None:
                    conn.sock.settimeout(
                        min(
                            source.limits.request_timeout_seconds,
                            max(0.01, deadline - time.monotonic()),
                        )
                    )
                chunk = response.read1(min(65536, budget + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > budget:
                raise PipelineError("LIMIT_REACHED", "response exceeds byte budget")
            return Response(
                bytes(body),
                response.status,
                response.getheader("Content-Type", "").split(";")[0].lower(),
                url,
            )
        except (OSError, http.client.HTTPException) as exc:
            raise PipelineError("FETCH_FAILED", "acquisition failed", retryable=True) from exc
        finally:
            conn.close()
    raise PipelineError("REDIRECT_LIMIT", "redirect limit exceeded")


def _discovered(snapshot: bytes, source: SourceSpec, page_url: str) -> list[str]:
    navigation = source.navigation
    if navigation is None:
        return []
    if navigation.kind.startswith("html_"):
        try:
            nodes = BeautifulSoup(snapshot, "html.parser").select(navigation.value)
        except Exception as exc:
            raise PipelineError("NAVIGATION_INVALID", "invalid navigation selector") from exc
        values = [node.get(navigation.attribute) for node in nodes]
    else:
        value = pointer_get(loads(snapshot), navigation.value)
        values = [] if value is MISSING else value if isinstance(value, list) else [value]
    if navigation.kind.endswith("_next") and len([value for value in values if value]) > 1:
        raise PipelineError("NAVIGATION_AMBIGUOUS", "next link matched more than one URL")
    urls = []
    for value in values:
        if not isinstance(value, str):
            raise PipelineError("NAVIGATION_INVALID", "navigation links must be strings")
        if not value:
            continue
        url = urljoin(page_url, value)
        parsed, _, _ = validated_target(url, source.allowed_domains)
        if urlsplit(source.url).scheme == "https" and parsed.scheme != "https":
            raise PipelineError("REDIRECT_DENIED", "HTTPS downgrade rejected")
        urls.append(url)
    return urls


def acquire_pages(source: SourceSpec, *, retained: list[PageCapture] | None = None, reserve=None):
    if source.mode not in {"html", "json"}:
        raise PipelineError(
            "CAPABILITY_UNSUPPORTED",
            "live browser/PDF acquisition requires a dedicated hardened worker; canonical artifact replay is supported",
        )
    deadline = time.monotonic() + source.limits.deadline_seconds
    policies: dict[str, RobotFileParser | None] = {}
    last_request: dict[str, float] = {}

    def wait_turn(origin: str, delay: float):
        delay = max(delay, source.min_interval_seconds)
        if reserve is not None:
            # Robots rules learned after fetching robots.txt must still delay
            # this worker's next request before it takes a shared slot.
            local_remaining = delay - (time.monotonic() - last_request.get(origin, -1e9))
            if local_remaining > 0:
                if time.monotonic() + local_remaining >= deadline:
                    raise PipelineError(
                        "LIMIT_REACHED", "robots delay exceeds acquisition deadline"
                    )
                time.sleep(local_remaining)
            remaining = reserve(origin, delay, deadline - time.monotonic())
        else:
            remaining = delay - (time.monotonic() - last_request.get(origin, -1e9))
        if remaining > 0:
            if time.monotonic() + remaining >= deadline:
                raise PipelineError("LIMIT_REACHED", "robots delay exceeds acquisition deadline")
            time.sleep(remaining)
        last_request[origin] = time.monotonic()

    def authorize(url):
        parsed, host, port = validated_target(url, source.allowed_domains)
        origin = f"{parsed.scheme}://{host}"
        if port != (443 if parsed.scheme == "https" else 80):
            origin += f":{port}"
        if origin not in policies:
            wait_turn(origin, 0)
            robots = fetch(origin + "/robots.txt", source, 512_000, deadline=deadline)
            if robots.status in {404, 410}:
                policies[origin] = None
            elif robots.status == 200:
                parsed_policy = RobotFileParser()
                parsed_policy.parse(robots.body.decode("utf-8", errors="replace").splitlines())
                policies[origin] = parsed_policy
            else:
                raise PipelineError(
                    "ROBOTS_UNAVAILABLE",
                    "robots policy could not be verified",
                    retryable=robots.status >= 500,
                )
        policy = policies[origin]
        if policy is not None:
            if not policy.can_fetch(USER_AGENT, url):
                raise PipelineError("ROBOTS_DENIED", "source policy disallows acquisition")
            delay = float(policy.crawl_delay(USER_AGENT) or 0)
            rate = policy.request_rate(USER_AGENT)
            if rate:
                delay = max(delay, rate.seconds / rate.requests)
        else:
            delay = 0
        wait_turn(origin, delay)

    allowed = {
        "html": {"text/html", "application/xhtml+xml"},
        "json": {"application/json", "text/json"},
    }
    queue = [source.url]
    seen: set[str] = set()
    retained = retained or []
    total_bytes = 0
    index = 0
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        if index >= source.limits.max_pages:
            raise PipelineError("LIMIT_REACHED", "discovery exceeds page budget")
        seen.add(url)
        if index < len(retained):
            page = retained[index]
            if (page.request_url or page.url) != url:
                raise PipelineError(
                    "SNAPSHOT_CORRUPT", "retained page order differs from discovery"
                )
        else:
            response = fetch(url, source, authorize=authorize, deadline=deadline)
            if response.status != 200:
                raise PipelineError(
                    "HTTP_STATUS",
                    f"source returned HTTP {response.status}",
                    response.status == 429 or response.status >= 500,
                )
            if response.content_type not in allowed[source.mode]:
                raise PipelineError(
                    "MIME_UNSUPPORTED", "source content type does not match requested mode"
                )
            page = PageCapture(
                response.final_url,
                response.body,
                normalize(response.body, source.mode),
                request_url=url,
            )
        total_bytes += len(page.raw)
        if total_bytes > source.limits.max_bytes:
            raise PipelineError("LIMIT_REACHED", "collection exceeds byte budget")
        yield page
        index += 1
        for discovered in _discovered(page.snapshot, source, page.url):
            if discovered not in seen and discovered not in queue:
                queue.append(discovered)
        if len(queue) + index > source.limits.max_pages:
            raise PipelineError("LIMIT_REACHED", "discovery exceeds page budget")
    if index < len(retained):
        raise PipelineError("SNAPSHOT_CORRUPT", "retained page count differs from discovery")


def acquire(source: SourceSpec) -> tuple[bytes, bytes]:
    if source.navigation:
        raise PipelineError(
            "CAPABILITY_UNSUPPORTED", "navigating sources require page-aware acquisition"
        )
    page = next(acquire_pages(source))
    return page.raw, page.snapshot


def normalize(raw: bytes, mode: str) -> bytes:
    """Rebuild a canonical capture from retained raw bytes without fetching again."""
    if mode not in {"html", "json"}:
        raise PipelineError("CAPABILITY_UNSUPPORTED", "normalization requires HTML or JSON")
    try:
        text = unicodedata.normalize(
            "NFC", raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        )
        return canonical(loads(text)) if mode == "json" else text.encode()
    except (UnicodeError, ValueError) as exc:
        raise PipelineError(
            "ENCODING_UNSUPPORTED", "source must contain UTF-8 canonicalizable content"
        ) from exc
