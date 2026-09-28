"""Kaggle-only infrastructure smoke test, NOT a quality benchmark."""

import os
from pathlib import Path

if not Path("/kaggle/working").is_dir():
    raise SystemExit("Remote-only: run through Kaggle MCP. Do not execute on a workstation.")

os.environ.update(
    {
        "HF_HOME": "/tmp/evidence-pipeline-model-cache",
        "TORCH_HOME": "/tmp/evidence-pipeline-torch-cache",
        "USE_TF": "0",
        "TOKENIZERS_PARALLELISM": "false",
        "PIP_NO_CACHE_DIR": "1",
    }
)
import json
import platform
import subprocess
import sys
import time
import traceback

revision = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
report = {
    "schema_version": "kaggle-smoke/v1",
    "kind": "infrastructure_smoke",
    "quality_benchmark": False,
    "model": "convaiinnovations/laya",
    "revision": revision,
    "runtime": "laya==0.3.20",
    "data": "synthetic smoke cases; no gold-label claims",
    "status": "running",
    "python": platform.python_version(),
}
out = Path("/kaggle/working/smoke-report.json")


def save():
    out.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")


save()
try:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "laya==0.3.20"],
        check=True,
    )
    import laya
    import torch

    report["torch"] = torch.__version__
    report["cuda_available"] = torch.cuda.is_available()
    if not torch.cuda.is_available():
        raise RuntimeError("GPU unavailable; this smoke test requires a Kaggle GPU")
    report["gpu"] = torch.cuda.get_device_name(0)
    started = time.perf_counter()
    # PyPI 0.3.20 predates the revision argument shown on upstream main.
    # Pin with the Hub client, entirely inside Kaggle, then load the local snapshot.
    from huggingface_hub import snapshot_download

    checkpoint = snapshot_download(
        "convaiinnovations/laya", revision=revision, ignore_patterns=["*/*"]
    )
    agent = laya.load(checkpoint, device="cuda", fast=False)
    torch.cuda.synchronize()
    report["cold_load_seconds"] = time.perf_counter() - started
    questions = {
        "category": {
            "type": "choice",
            "instructions": "Classify this product by its primary function.",
            "criteria": {
                "power_tool": "Electric drills and cutting tools",
                "sealant": "Construction sealants and adhesives",
                "other": "Products outside the supported categories",
            },
        }
    }
    cases = [
        {
            "name": "Synthetic cordless drill",
            "description": "An 18 volt drill for drilling timber and metal.",
        },
        {
            "name": "Synthetic joint sealant",
            "description": "Elastic polyurethane sealant for concrete expansion joints.",
        },
        {"name": "Synthetic ceramic mug", "description": "A ceramic cup for drinking tea."},
    ]
    timings = []
    predictions = []
    for case in cases:
        started = time.perf_counter()
        answer = agent.predict(case, questions)
        torch.cuda.synchronize()
        timings.append((time.perf_counter() - started) * 1000)
        predictions.append(answer)
    report.update(
        status="completed",
        latency_ms=timings,
        predictions=predictions,
        peak_gpu_memory_bytes=torch.cuda.max_memory_allocated(),
    )
except Exception as exc:
    report.update(
        status="failed",
        error_type=type(exc).__name__,
        error=str(exc),
        traceback=traceback.format_exc(),
    )
    raise
finally:
    save()
    print(json.dumps(report, indent=2, default=str))
