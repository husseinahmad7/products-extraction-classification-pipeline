import socket
from unittest.mock import patch

import pytest

from product_pipeline.acquisition import resolve_public
from product_pipeline.errors import PipelineError


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "ff02::1",
        "64:ff9b::a00:1",
        "2002:0808:0808::1",
    ],
)
def test_private_dns_is_denied(address):
    with patch.object(
        socket, "getaddrinfo", return_value=[(None, None, None, None, (address, 443))]
    ):
        with pytest.raises(PipelineError) as error:
            resolve_public("https://example.com", ["example.com"])
        assert error.value.code == "ADDRESS_DENIED"


def test_domain_and_port_denied():
    with pytest.raises(PipelineError):
        resolve_public("https://evil.example", ["example.com"])
    with pytest.raises(PipelineError):
        resolve_public("https://example.com:8080", ["example.com"])


def test_connection_pins_public_address():
    with patch.object(
        socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]
    ):
        assert resolve_public("https://example.com", ["example.com"]) == (
            "example.com",
            443,
            "93.184.216.34",
        )
