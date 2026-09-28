import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = runpy.run_path(str(ROOT / "scripts" / "release_gate.py"))


def test_current_release_remains_blocked():
    manifest = json.loads((ROOT / "benchmarks/release-manifest.json").read_text())
    assert GATE["failures"](manifest)


def test_smoke_or_ai_report_cannot_unlock_release():
    for report_name in ("laya-smoke.json", "ai-reviewed-diagnostic.json"):
        report = json.loads((ROOT / "benchmarks/reports" / report_name).read_text(encoding="utf-8"))
        manifest = {
            "schema_version": "release-gate/v1",
            "status": "ready",
            "classification_benchmark": {
                "status": "completed",
                "notebook": report["notebook"],
                "version": report["notebook_version"],
                "report_sha256": "a" * 64,
            },
            "capabilities": dict.fromkeys(GATE["REQUIRED"], True),
        }
        assert "report is not an independent-gold comparative Kaggle benchmark" in GATE["failures"](
            manifest, report, "a" * 64
        )


def test_manifest_metadata_without_report_never_unlocks():
    manifest = {
        "schema_version": "release-gate/v1",
        "status": "ready",
        "classification_benchmark": {
            "status": "completed",
            "notebook": "https://www.kaggle.com/code/owner/benchmark",
            "version": 1,
            "report_sha256": "a" * 64,
        },
        "capabilities": dict.fromkeys(GATE["REQUIRED"], True),
    }
    assert "required comparative report artifact is missing" in GATE["failures"](manifest)
