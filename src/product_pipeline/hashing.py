import hashlib
from typing import Any

import rfc8785


def canonical(value: Any) -> bytes:
    return rfc8785.dumps(value)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def object_hash(value: Any) -> str:
    return digest(canonical(value))
