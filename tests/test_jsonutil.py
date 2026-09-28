import pytest
from pydantic import ValidationError

from product_pipeline.contracts import TargetSchema
from product_pipeline.engine import extract
from product_pipeline.errors import PipelineError
from product_pipeline.jsonutil import loads


@pytest.mark.parametrize(
    "text",
    ['{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":1,"x":2}', "[" * 100 + "0" + "]" * 100],
)
def test_reject_ambiguous_or_unbounded_json(text):
    with pytest.raises(ValueError):
        loads(text)


def test_snapshot_rejects_nonfinite_before_hashing(recipe):
    with pytest.raises(PipelineError, match="canonical artifact"):
        extract(b'{"items":[{"sku":"A","name":"x","price":NaN}]}', recipe)


def test_schema_rejects_unbounded_regex():
    with pytest.raises(ValidationError, match="regex"):
        TargetSchema(
            id="unsafe",
            definition={
                "type": "object",
                "properties": {"x": {"type": "string", "pattern": "(a+)+$"}},
            },
        )


def test_source_rejects_query_credentials(source):
    with pytest.raises(ValidationError, match="secret query"):
        type(source).model_validate(
            {**source.model_dump(), "url": source.url + "?api_key=not-a-real-key"}
        )
