"""No-key taxonomy suggestions. Similarity is not calibrated probability."""

import math
import re
from collections import Counter
from typing import Any

from .contracts import TaxonomySpec
from .hashing import object_hash


def rank_candidates(text: str, taxonomy: TaxonomySpec, limit: int = 10) -> dict:
    by_id = {n.id: n for n in taxonomy.nodes}
    parents = {n.parent_id for n in taxonomy.nodes}
    paths = {}
    for node in taxonomy.nodes:
        if node.id in parents:
            continue
        path = [node]
        while path[0].parent_id is not None:
            path.insert(0, by_id[path[0].parent_id])
        paths[node.id] = path

    def tokens(value):
        return Counter(re.findall(r"\w+", value.casefold()))

    documents = {
        key: tokens(" ".join(n.label + " " + " ".join(n.aliases) for n in path))
        for key, path in paths.items()
    }
    query = tokens(text)
    df = Counter(token for document in documents.values() for token in document)
    idf = {token: math.log((1 + len(documents)) / (1 + count)) + 1 for token, count in df.items()}
    q = {token: count * idf.get(token, 0) for token, count in query.items()}
    qnorm = math.sqrt(sum(v * v for v in q.values()))
    scores: list[dict[str, Any]] = []
    for key, document in documents.items():
        weights = {token: count * idf[token] for token, count in document.items()}
        norm = math.sqrt(sum(v * v for v in weights.values()))
        similarity = (
            sum(q.get(t, 0) * w for t, w in weights.items()) / (qnorm * norm)
            if qnorm and norm
            else 0
        )
        scores.append(
            {
                "id": key,
                "path": [n.id for n in paths[key]],
                "label": paths[key][-1].label,
                "similarity": similarity,
            }
        )
    return {
        "taxonomy_hash": object_hash(taxonomy.model_dump()),
        "state": "abstained",
        "reason": "UNCALIBRATED",
        "selected_path": None,
        "candidates": sorted(scores, key=lambda s: (-s["similarity"], s["id"]))[:limit],
    }


def wilson_lower(correct: int, accepted: int, z: float = 1.959963984540054) -> float:
    if not 0 <= correct <= accepted:
        raise ValueError("invalid counts")
    if not accepted:
        return 0.0
    p = correct / accepted
    return (
        p
        + z * z / (2 * accepted)
        - z * math.sqrt(p * (1 - p) / accepted + z * z / (4 * accepted * accepted))
    ) / (1 + z * z / accepted)
