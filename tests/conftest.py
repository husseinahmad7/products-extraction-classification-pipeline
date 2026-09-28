import pytest

from product_pipeline.contracts import Recipe, SourceSpec, TargetSchema


@pytest.fixture
def target():
    return TargetSchema(
        id="product",
        definition={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "name": {"type": "string"},
                "price": {"type": "number"},
            },
            "required": ["sku", "name"],
            "additionalProperties": False,
        },
    )


@pytest.fixture
def source():
    return SourceSpec(
        id="catalog",
        name="Synthetic catalog",
        url="https://example.com/catalog",
        allowed_domains=["example.com"],
        mode="json",
    )


@pytest.fixture
def recipe(target):
    return Recipe.model_validate(
        {
            "source_id": "catalog",
            "mode": "json",
            "record_scope": {"type": "items_path", "value": "$.items[*]"},
            "identity_fields": ["/sku"],
            "target": target.model_dump(),
            "fields": {
                "/sku": {"locator": {"type": "json_pointer", "value": "/sku"}, "required": True},
                "/name": {"locator": {"type": "json_pointer", "value": "/name"}, "required": True},
                "/price": {"locator": {"type": "json_pointer", "value": "/price"}},
            },
        }
    )


@pytest.fixture
def snapshot():
    return b'{"items":[{"sku":"A","name":"Cordless drill","price":42},{"sku":"B","name":"Anchor adhesive","price":18}]}'
