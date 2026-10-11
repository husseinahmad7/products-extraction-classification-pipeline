"""Real Ed25519 trust-root tests; no cloud credentials or inference dependencies."""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from product_pipeline.errors import PipelineError
from product_pipeline.server.identity import JWKSVerifier

ISSUER = "https://trusted.example.com/auth"


@pytest.fixture
def signed():
    key = Ed25519PrivateKey.generate()
    verifier = JWKSVerifier(ISSUER, ISSUER + "/.well-known/jwks.json", "pipeline")
    verifier.keys = {"current": key.public_key()}
    verifier.fetched = time.monotonic()

    def token(**overrides):
        timestamp = int(time.time())
        claims = {
            "iss": ISSUER,
            "sub": "alice",
            "aud": "pipeline",
            "iat": timestamp,
            "exp": timestamp + 900,
            **overrides,
        }
        return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "current"})

    return verifier, token, key


def test_valid_signature_and_pinned_issuer(signed):
    verifier, token, _ = signed
    assert verifier(token())["sub"] == "alice"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.example.com/auth"},
        {"aud": "other"},
        {"sub": ""},
        {"role": "anonymous"},
        {"role": "anon"},
        {"iat": "future"},
        {"exp": "past"},
        {"exp": "too-long"},
    ],
)
def test_invalid_verified_claims_rejected(signed, overrides):
    verifier, token, _ = signed
    mapped = {
        "future": int(time.time()) + 120,
        "past": int(time.time()) - 60,
        "too-long": int(time.time()) + 3600,
    }
    claims = {key: mapped.get(value, value) for key, value in overrides.items()}
    with pytest.raises(PipelineError, match="JWT"):
        verifier(token(**claims))


def test_wrong_signature_and_algorithm_rejected(signed):
    verifier, token, _ = signed
    value = jwt.decode(token(), options={"verify_signature": False})
    for forged in (
        jwt.encode(
            value, Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": "current"}
        ),
        jwt.encode(value, "not-an-ed25519-key", algorithm="HS256", headers={"kid": "current"}),
    ):
        with pytest.raises(PipelineError):
            verifier(forged)


def test_unknown_kid_cannot_force_refetch(signed, monkeypatch):
    verifier, token, key = signed
    claims = jwt.decode(token(), options={"verify_signature": False})

    def forbidden(*args, **kwargs):
        pytest.fail("unknown key spam must not force a fresh HTTP client inside cooldown")

    monkeypatch.setattr(httpx, "Client", forbidden)
    for i in range(3):
        with pytest.raises(PipelineError):
            verifier(jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": f"unknown-{i}"}))


def test_jwks_rotation_fetch_is_fixed_and_bounded(signed, monkeypatch):
    verifier, token, key = signed
    verifier.keys = {}
    verifier.fetched = float("-inf")
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    response = httpx.Response(
        200,
        json={
            "keys": [
                {
                    "kid": "current",
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "alg": "EdDSA",
                    "x": jwt.utils.base64url_encode(raw).decode(),
                }
            ]
        },
        request=httpx.Request("GET", verifier.url),
    )
    seen = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 5, "follow_redirects": False, "trust_env": False}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url):
            seen.append(url)
            return response

    monkeypatch.setattr(httpx, "Client", Client)
    assert verifier(token())["sub"] == "alice"
    assert verifier(token())["sub"] == "alice"
    assert seen == [ISSUER + "/.well-known/jwks.json"]
