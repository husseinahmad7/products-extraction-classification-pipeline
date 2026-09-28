"""Prepare a code-only private Kaggle payload after explicit upload approval.

Nothing is uploaded by this script. Its stdout contains unpublished source, so
only pass it to a private notebook after the repository owner approves that copy.
The allowlist excludes credentials, environment files, captures and datasets.
"""

import argparse
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-code-upload", action="store_true")
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--part", type=int, help="emit one 12,000-character source part")
    args = parser.parse_args()
    if not args.approve_code_upload:
        parser.error("explicit repository-owner approval is required before preparing source")
    root = Path(__file__).resolve().parents[1]
    names = {
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "NOTICE",
        "alembic.ini",
        "tests/conftest.py",
        "tests/test_postgres.py",
    }
    names.update(
        path.relative_to(root).as_posix()
        for path in (root / "src/product_pipeline").rglob("*")
        if path.is_file() and path.suffix in {".py", ".mako"}
    )
    archive = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name in sorted(names):
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("source allowlist must stay inside the repository")
            data = path.read_bytes()
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, data)
            manifest.append(
                {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            )
    payload = archive.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    notebook = (root / "benchmarks/kaggle_postgres_integration.py").read_text(encoding="utf-8")
    notebook = notebook.replace("__SOURCE_BUNDLE_BASE64__", base64.b64encode(payload).decode())
    notebook = notebook.replace("__SOURCE_BUNDLE_SHA256__", digest)
    result = {
        "source_bundle_sha256": digest,
        "bundle_bytes": len(payload),
        "files": manifest,
        "parts": (len(notebook) + 11999) // 12000,
    }
    if args.part is not None:
        if args.part < 0 or args.part >= result["parts"]:
            parser.error("part is out of range")
        result["text_part"] = notebook[args.part * 12000 : (args.part + 1) * 12000]
    elif not args.metadata_only:
        result["text"] = notebook
    print(json.dumps(result))


if __name__ == "__main__":
    main()
