"""Public contracts and deterministic execution. No models download on import."""

from .contracts import Recipe, SourceSpec, TargetSchema, TaxonomySpec
from .engine import extract, replay, semantic_diff

__all__ = [
    "Recipe",
    "SourceSpec",
    "TargetSchema",
    "TaxonomySpec",
    "extract",
    "replay",
    "semantic_diff",
]
__version__ = "0.1.0a1"
