from unittest.mock import patch

import pytest

from product_pipeline.acquisition import Response, acquire, acquire_pages, fetch, normalize
from product_pipeline.contracts import Navigation
from product_pipeline.errors import PipelineError


def test_robots_is_enforced_again_after_redirect(source):
    seen = []

    def fake_fetch(url, spec, max_bytes=None, *, authorize=None, deadline=None):
        seen.append(url)
        if url.endswith("robots.txt"):
            return Response(b"User-agent: *\nDisallow: /private\n", 200, "text/plain", url)
        authorize(url)
        authorize("https://example.com/private/data")
        pytest.fail("the redirect should have been denied")

    with patch("product_pipeline.acquisition.fetch", side_effect=fake_fetch):
        with pytest.raises(PipelineError) as error:
            acquire(source)
    assert error.value.code == "ROBOTS_DENIED"
    assert len(seen) == 2


def test_robots_failure_is_not_silently_ignored(source):
    def fake_fetch(url, spec, max_bytes=None, *, authorize=None, deadline=None):
        if authorize:
            authorize(url)
        return Response(b"unavailable", 503, "text/plain", url)

    with patch("product_pipeline.acquisition.fetch", side_effect=fake_fetch):
        with pytest.raises(PipelineError) as error:
            acquire(source)
    assert error.value.code == "ROBOTS_UNAVAILABLE" and error.value.retryable


def test_robots_delay_cannot_exceed_deadline(source):
    def fake_fetch(url, spec, max_bytes=None, *, authorize=None, deadline=None):
        if url.endswith("robots.txt"):
            return Response(b"User-agent: *\nCrawl-delay: 10000\n", 200, "text/plain", url)
        authorize(url)
        pytest.fail("delay should be rejected without sleeping")

    with patch("product_pipeline.acquisition.fetch", side_effect=fake_fetch):
        with pytest.raises(PipelineError, match="robots delay"):
            acquire(source)


def test_expired_deadline_does_not_open_a_connection(source):
    with patch("product_pipeline.acquisition.resolve_public") as resolver:
        with pytest.raises(PipelineError, match="deadline"):
            fetch(source.url, source, deadline=-1)
    resolver.assert_not_called()


def test_fetch_checks_redirect_authorization_and_byte_limit(source):
    class Reply:
        status = 200

        def getheader(self, key, default=None):
            return "application/json" if key == "Content-Type" else default

        def read1(self, amount):
            return b"x" * amount

    class Connection:
        sock = None

        def __init__(self, *args):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    checked = []
    with (
        patch("product_pipeline.acquisition.PinnedConnection", Connection),
        patch(
            "product_pipeline.acquisition.resolve_public",
            return_value=("example.com", 443, "93.184.216.34"),
        ),
    ):
        with pytest.raises(PipelineError, match="byte budget"):
            fetch(source.url, source, max_bytes=4, authorize=checked.append)
    assert checked == [source.url]


def test_normalization_replays_without_network():
    assert normalize(b'{ "b":2, "a":1 }\r\n', "json") == b'{"a":1,"b":2}'
    assert normalize(b"<p>line\r\nnext</p>", "html") == b"<p>line\nnext</p>"
    with pytest.raises(PipelineError, match="UTF-8"):
        normalize(b"\xff", "html")


def test_bounded_json_navigation_and_deduplication(source):
    source = source.model_copy(update={"navigation": Navigation(kind="json_links", value="/links")})
    bodies = {
        source.url: b'{"links":["/detail/1","/detail/1","/detail/2"]}',
        "https://example.com/detail/1": b'{"links":[],"sku":"A"}',
        "https://example.com/detail/2": b'{"links":[],"sku":"B"}',
    }
    seen = []

    def fake_fetch(url, spec, max_bytes=None, *, authorize=None, deadline=None):
        seen.append(url)
        if url.endswith("robots.txt"):
            return Response(b"User-agent: *\nAllow: /\n", 200, "text/plain", url)
        authorize(url)
        return Response(bodies[url], 200, "application/json", url)

    with (
        patch("product_pipeline.acquisition.fetch", side_effect=fake_fetch),
        patch("product_pipeline.acquisition.time.sleep"),
    ):
        pages = list(acquire_pages(source))
    assert [page.url for page in pages] == list(bodies)
    assert len([url for url in seen if not url.endswith("robots.txt")]) == 3


def test_navigation_budget_fails_before_partial_collection(source):
    source = source.model_copy(
        update={
            "navigation": Navigation(kind="json_links", value="/links"),
            "limits": source.limits.model_copy(update={"max_pages": 2}),
        }
    )

    def fake_fetch(url, spec, max_bytes=None, *, authorize=None, deadline=None):
        if url.endswith("robots.txt"):
            return Response(b"User-agent: *\nAllow: /\n", 200, "text/plain", url)
        authorize(url)
        return Response(b'{"links":["/a","/b"]}', 200, "application/json", url)

    with (
        patch("product_pipeline.acquisition.fetch", side_effect=fake_fetch),
        patch("product_pipeline.acquisition.time.sleep"),
    ):
        with pytest.raises(PipelineError, match="page budget"):
            list(acquire_pages(source))


def test_auth_reference_is_resolved_only_for_original_origin(source, monkeypatch):
    source = source.model_copy(
        update={"auth_ref": "env:SOURCE_TOKEN", "allowed_domains": ["example.com", "other.example"]}
    )
    monkeypatch.setenv("SOURCE_TOKEN", "private-value")
    requests = []

    class Reply:
        def __init__(self, status):
            self.status = status
            self.read = False

        def getheader(self, key, default=None):
            if key == "Location" and self.status == 302:
                return "https://other.example/detail"
            if key == "Content-Type":
                return "application/json"
            return default

        def read1(self, amount):
            if self.status != 200 or self.read:
                return b""
            self.read = True
            return b"{}"

    class Connection:
        sock = None

        def __init__(self, host, *args):
            self.host = host

        def request(self, method, path, headers):
            requests.append((self.host, headers))

        def getresponse(self):
            return Reply(302 if self.host == "example.com" else 200)

        def close(self):
            pass

    with (
        patch("product_pipeline.acquisition.PinnedConnection", Connection),
        patch(
            "product_pipeline.acquisition.resolve_public",
            side_effect=lambda url, domains: (
                "example.com" if "example.com/catalog" in url else "other.example",
                443,
                "93.184.216.34",
            ),
        ),
    ):
        fetch(source.url, source)
    assert requests[0][1]["Authorization"] == "Bearer private-value"
    assert "Authorization" not in requests[1][1]


def test_redirect_credentials_are_rejected_before_origin_persistence(source):
    origins = []

    def fake_fetch(url, spec, max_bytes=None, *, authorize=None, deadline=None):
        if url.endswith("robots.txt"):
            return Response(b"User-agent: *\nAllow: /\n", 200, "text/plain", url)
        authorize(url)
        authorize("https://user:secret@example.com/private")
        pytest.fail("credential-bearing redirect was accepted")

    with (
        patch("product_pipeline.acquisition.fetch", side_effect=fake_fetch),
        patch("product_pipeline.acquisition.time.sleep"),
    ):
        with pytest.raises(PipelineError) as error:
            list(
                acquire_pages(
                    source, reserve=lambda origin, delay, remaining: origins.append(origin) or 0
                )
            )
    assert error.value.code == "URL_POLICY_DENIED"
    assert all("secret" not in origin for origin in origins)
