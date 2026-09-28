import argparse
import json
from pathlib import Path

from product_pipeline.contracts import Recipe, SourceSpec, TargetSchema, TaxonomySpec

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
root = Path("schemas")
if not args.check:
    root.mkdir(exist_ok=True)
failed = []
for name, model in {
    "recipe": Recipe,
    "source": SourceSpec,
    "target": TargetSchema,
    "taxonomy": TaxonomySpec,
}.items():
    path = root / (name + "-v1.json")
    text = (
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            failed.append(str(path))
    else:
        path.write_text(text, encoding="utf-8")
if failed:
    raise SystemExit("Generated schema drift: " + ", ".join(failed))
