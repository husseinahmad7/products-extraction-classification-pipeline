"""Emit a private Kaggle notebook payload; reads code only, never model/data files.

Run locally to stdout and send the JSON `text` through Kaggle MCP save_notebook.
Notebook execution itself is guarded and occurs only inside Kaggle.
"""

import argparse
import base64
import hashlib
import json
import runpy
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
DOCKER_IMAGE = "gcr.io/kaggle-private-byod/python@sha256:37c64f7dd9c54116ecd1bcc88817c5469b88387388fade02bfa8bf3fc647d461"


def notebook_payload(export_configuration=None):
    if export_configuration is not None:
        validate = runpy.run_path(str(DIRECTORY / "training_protocol.py"))[
            "validate_export_configuration"
        ]
        export_configuration = validate(export_configuration)
    filenames = ("training_protocol.py", "kaggle_training_comparison.py")
    files = {
        name: base64.b64encode((DIRECTORY / name).read_bytes()).decode("ascii")
        for name in filenames
    }
    code = (
        "from pathlib import Path\n"
        'if not Path("/kaggle/working").is_dir():\n'
        '    raise SystemExit("Remote-only: private Kaggle execution required")\n'
        "import base64, json, os, subprocess, sys\n"
        + (
            f"os.environ['EVIDENCE_TRAINING_EXPORT'] = {json.dumps(json.dumps(export_configuration))}\n"
            if export_configuration
            else ""
        )
        + 'directory = Path("/tmp/evidence-training-code")\n'
        "directory.mkdir(parents=True, exist_ok=True)\n"
        f"files = json.loads({json.dumps(json.dumps(files))})\n"
        "for filename, encoded in files.items():\n"
        "    (directory / filename).write_bytes(base64.b64decode(encoded))\n"
        'subprocess.run([sys.executable, str(directory / "kaggle_training_comparison.py")], check=True)\n'
    )
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}
        },
        "cells": [
            {
                "cell_type": "markdown",
                "id": "training-provenance",
                "metadata": {},
                "source": [
                    "# Private exploratory training comparison\ncertification_eligible=false; auto_accept_enabled=false. "
                    + (
                        "Reviewer-declared immutable correction snapshot; inspection history is not independently attested. "
                        if export_configuration
                        else "The publisher-labelled test has previously been inspected. "
                    )
                    + "Training/data/model downloads occur "
                    "only in Kaggle; notebook outputs contain metrics and ID-only predictions, never weights or CSVs.\n"
                ],
            },
            {
                "cell_type": "code",
                "id": "remote-only-runner",
                "metadata": {},
                "execution_count": None,
                "outputs": [],
                "source": code.splitlines(keepends=True),
            },
        ],
    }
    return {
        "kernel_type": "notebook",
        "text": json.dumps(notebook),
        "docker_image": DOCKER_IMAGE,
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": True,
        "machine_shape": "NvidiaTeslaT4",
        "session_timeout_seconds": 5700,
        "source_hashes": {
            name: hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest() for name in filenames
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-url", help="HTTPS immutable /v1/datasets/{id}/export URI")
    parser.add_argument("--dataset-hash", help="Expected RFC8785 snapshot SHA256")
    parser.add_argument("--taxonomy-hash", help="Expected frozen taxonomy SHA256")
    parser.add_argument(
        "--text-pointer",
        action="append",
        help="Explicit record.data RFC6901 input pointer; repeat as needed",
    )
    parser.add_argument("--bearer-secret-name", help="Kaggle Secrets name only; never pass a token")
    args = parser.parse_args()
    configuration = None
    if args.dataset_url:
        configuration = {
            "url": args.dataset_url,
            "dataset_hash": args.dataset_hash,
            "taxonomy_hash": args.taxonomy_hash,
            "text_pointers": args.text_pointer,
            "bearer_secret_name": args.bearer_secret_name,
        }
    elif any((args.dataset_hash, args.taxonomy_hash, args.text_pointer, args.bearer_secret_name)):
        parser.error("Export hashes, selectors and secret name require --dataset-url")
    try:
        print(json.dumps(notebook_payload(configuration)))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
