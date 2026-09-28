"""Strict JSON at trust boundaries: duplicate keys and nonfinite numbers are invalid."""

import json
import math
from typing import Any


def bounded_tree(value: Any, *, max_depth: int = 32, max_nodes: int = 100000):
    pending = [(value, 0)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > max_nodes or depth > max_depth:
            raise ValueError("JSON exceeds structural complexity budget")
        if isinstance(node, float) and not math.isfinite(node):
            raise ValueError("nonfinite JSON number")
        if isinstance(node, dict):
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)
    return value


def loads(value: str | bytes) -> Any:
    def constant(_value):
        raise ValueError("nonfinite JSON number")

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, parse_constant=constant, object_pairs_hook=pairs)
        return bounded_tree(parsed)
    except RecursionError as exc:
        raise ValueError("JSON exceeds depth budget") from exc
