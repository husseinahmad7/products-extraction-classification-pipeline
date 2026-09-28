import json
from pathlib import Path

import pytest

from product_pipeline.cli import main


@pytest.mark.parametrize("example", ["catalog", "documentation"])
def test_offline_example_and_replay(example, tmp_path, capsys):
    root = Path(__file__).resolve().parents[1]
    recipe = str(root / "examples" / f"{example}-recipe.yaml")
    snapshot = str(root / "examples" / f"{example}.json")
    output = str(tmp_path / "result.json")
    assert main(["validate", recipe]) == 0
    assert main(["extract", recipe, snapshot, "--output", output]) == 0
    result = json.loads(Path(output).read_text(encoding="utf-8"))
    assert len(result["records"]) == 2
    assert result["records"][0]["evidence"]
    assert main(["replay", recipe, snapshot, output]) == 0
    capsys.readouterr()


def test_cli_schema_and_invalid_file(tmp_path, capsys):
    assert main(["schema", "recipe"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["mode"]
    assert main(["validate", str(tmp_path / "missing.yaml")]) == 2
    assert "error" in capsys.readouterr().err
