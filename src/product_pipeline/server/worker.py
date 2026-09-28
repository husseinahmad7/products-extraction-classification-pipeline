"""Worker orchestration. Artifact writes stage immutable bytes; only fenced DB commits publish."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import update

from product_pipeline.acquisition import PageCapture, acquire, acquire_pages, normalize
from product_pipeline.compiler import compile_snapshot
from product_pipeline.contracts import ExtractionResult, Recipe, SourceSpec, TargetSchema
from product_pipeline.engine import extract, extract_pages, replay, replay_pages, semantic_diff
from product_pipeline.errors import PipelineError
from product_pipeline.hashing import canonical

from .db import Database, DatasetHead, Resource, audit, current_job, database_now, get_resource, uid

log = logging.getLogger(__name__)


def publish(
    session, workspace: str, run: Resource, result_key: str, expected_version: int, actor: str
):
    revision_id = uid()
    source_id = run.data["source_id"]
    updated = session.execute(
        update(DatasetHead)
        .where(
            DatasetHead.workspace == workspace,
            DatasetHead.source_id == source_id,
            DatasetHead.version == expected_version,
        )
        .values(revision_id=revision_id, version=expected_version + 1)
    )
    if updated.rowcount != 1:
        raise PipelineError("PUBLICATION_CONFLICT", "dataset advanced; rerun against the new head")
    session.add(
        Resource(
            workspace=workspace,
            kind="revision",
            id=revision_id,
            data={
                "source_id": source_id,
                "run_id": run.id,
                "recipe_id": run.data["recipe_id"],
                "result_key": result_key,
                "snapshot_hash": run.data["snapshot_hash"],
                "raw_hash": run.data.get("raw_hash"),
                "version": expected_version + 1,
            },
        )
    )
    run.data = {
        **run.data,
        "state": "succeeded",
        "revision_id": revision_id,
        "result_key": result_key,
    }
    run.version += 1
    audit(session, workspace, actor, "dataset.published", revision_id, {"run_id": run.id})


class Worker:
    def __init__(self, database: Database, store, owner: str | None = None, acquire_fn=acquire):
        self.db, self.store = database, store
        self.owner = owner or uid()
        self.live_pages = acquire_fn is acquire
        if self.live_pages:

            def acquire_one(source):
                page = next(acquire_pages(source, reserve=self.db.reserve_origin))
                return page.raw, page.snapshot

            self.acquire = acquire_one
        else:
            self.acquire = acquire_fn

    @contextmanager
    def heartbeats(self, claim):
        stop = threading.Event()

        def pulse():
            while not stop.wait(15):
                try:
                    if not self.db.heartbeat(claim):
                        return
                except Exception:
                    log.warning("heartbeat failed job=%s", claim["id"])
                    return

        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)

    def once(self) -> bool:
        workspace = getattr(self.store, "workspace", None)
        self.db.dispatch_due_schedules(workspace=workspace)
        claim = self.db.claim(
            self.owner, kinds=("run", "compile", "validate", "replay"), workspace=workspace
        )
        if claim is None:
            return False
        try:
            with self.heartbeats(claim):
                self.execute(claim)
        except PipelineError as exc:
            if exc.code != "LEASE_LOST":
                self.fail(claim, exc)
        except Exception:
            log.exception("worker failed job=%s", claim["id"])
            self.fail(
                claim,
                PipelineError(
                    "WORKER_ERROR", "worker failed; inspect server logs using the operation ID"
                ),
            )
        return True

    def fail(self, claim, error: PipelineError):
        try:
            with self.db.sessions.begin() as session:
                job = current_job(session, claim)
                paused = error.code == "QUOTA_EXCEEDED"
                retry = not paused and error.retryable and job.attempts < 3
                job.state = "paused" if paused else "queued" if retry else "failed"
                if paused:
                    # Waiting for operator capacity is not a worker-crash retry.
                    job.attempts = max(0, job.attempts - 1)
                job.owner, job.lease_until = None, None
                job.error = error.problem()
                job.available = database_now(session) + timedelta(seconds=2**job.attempts)
                if job.kind == "run":
                    run = get_resource(session, job.workspace, "run", job.payload["run_id"])
                    run.data = {
                        **run.data,
                        "state": job.state,
                        "error": error.problem(),
                    }
                    run.version += 1
                audit(
                    session,
                    job.workspace,
                    self.owner,
                    "job.paused" if paused else "job.retry" if retry else "job.failed",
                    job.id,
                    {"code": error.code, "generation": job.generation},
                )
        except PipelineError as exc:
            if exc.code != "LEASE_LOST":
                raise

    def execute(self, claim):
        workspace, payload = claim["workspace"], claim["payload"]
        with self.db.sessions.begin() as session:
            current_job(session, claim)
            if claim["kind"] == "run":
                run = get_resource(session, workspace, "run", payload["run_id"])
                run.data = {**run.data, "state": "running"}
                run.version += 1
                run_data = dict(run.data)
                source = SourceSpec.model_validate(
                    get_resource(session, workspace, "source", run_data["source_id"]).data
                )
                recipe = Recipe.model_validate(
                    get_resource(session, workspace, "recipe", run_data["recipe_id"]).data["spec"]
                )
                head = session.get(DatasetHead, (workspace, source.id))
                if head is None:
                    raise PipelineError("STATE_CORRUPT", "source has no dataset head")
                expected_version = head.version
                previous_key = (
                    get_resource(session, workspace, "revision", head.revision_id).data[
                        "result_key"
                    ]
                    if head.revision_id
                    else None
                )
            elif claim["kind"] == "compile":
                source = SourceSpec.model_validate(
                    get_resource(session, workspace, "source", payload["source_id"]).data
                )
                target = TargetSchema.model_validate(
                    get_resource(session, workspace, "schema", payload["target_id"]).data
                )
            elif claim["kind"] == "validate":
                recipe = Recipe.model_validate(
                    get_resource(session, workspace, "recipe", payload["recipe_id"]).data["spec"]
                )
                source = SourceSpec.model_validate(
                    get_resource(session, workspace, "source", recipe.source_id).data
                )
            elif claim["kind"] == "replay":
                run_data = dict(get_resource(session, workspace, "run", payload["run_id"]).data)
                recipe = Recipe.model_validate(
                    get_resource(session, workspace, "recipe", run_data["recipe_id"]).data["spec"]
                )
                source = SourceSpec.model_validate(
                    get_resource(session, workspace, "source", run_data["source_id"]).data
                )

        if claim["kind"] == "compile":
            proposal = compile_snapshot(
                self.store.get(payload["snapshot_hash"]), source, target, payload["identity_fields"]
            )
            proposal = {
                **proposal,
                "source_id": source.id,
                "target_id": target.id,
                "snapshot_hash": payload["snapshot_hash"],
            }
            with self.db.sessions.begin() as session:
                job = current_job(session, claim)
                draft_id = uid()
                session.add(Resource(workspace=workspace, kind="draft", id=draft_id, data=proposal))
                job.state, job.payload = (
                    "succeeded",
                    {**job.payload, "result": {"kind": "draft", "id": draft_id}},
                )
                audit(session, workspace, self.owner, "recipe.proposed", draft_id)
            return

        if claim["kind"] == "validate":
            snapshot = self.store.get(payload["snapshot_hash"])
            result = extract(snapshot, recipe, workspace, source.url)
            replay(snapshot, recipe, result, workspace_id=workspace, source_url=source.url)
            valid = bool(result.records) and not result.quarantined
            with self.db.sessions.begin() as session:
                job = current_job(session, claim)
                row = get_resource(session, workspace, "recipe", payload["recipe_id"])
                row.data = {
                    **row.data,
                    "state": "review_required" if valid else "invalid",
                    "validation": {
                        "valid": valid,
                        "records": len(result.records),
                        "quarantined": len(result.quarantined),
                        "result_hash": result.result_hash,
                        "snapshot_hash": payload["snapshot_hash"],
                    },
                }
                row.version += 1
                job.state, job.payload = (
                    "succeeded",
                    {**job.payload, "result": {"kind": "recipe", "id": row.id}},
                )
                audit(session, workspace, self.owner, "recipe.validated", row.id, {"valid": valid})
            return

        if claim["kind"] == "replay":
            if not run_data.get("result_key"):
                raise PipelineError("REPLAY_UNAVAILABLE", "run has no retained extraction result")
            expected = ExtractionResult.model_validate_json(self.store.get(run_data["result_key"]))
            if run_data.get("pages"):
                pages = run_data["pages"]
                manifest = canonical(
                    [{"url": page["url"], "snapshot_hash": page["snapshot_hash"]} for page in pages]
                )
                if self.store.get(run_data["snapshot_hash"]) != manifest:
                    raise PipelineError(
                        "SNAPSHOT_CORRUPT", "page manifest differs from retained evidence"
                    )
                replay_pages(
                    [(self.store.get(page["snapshot_hash"]), page["url"]) for page in pages],
                    recipe,
                    expected,
                    workspace_id=workspace,
                )
            else:
                replay(
                    self.store.get(run_data["snapshot_hash"]),
                    recipe,
                    expected,
                    workspace_id=workspace,
                    source_url=source.url,
                )
            with self.db.sessions.begin() as session:
                job = current_job(session, claim)
                report_id = uid()
                report = {
                    "run_id": payload["run_id"],
                    "result_hash": expected.result_hash,
                    "status": "matched",
                    "runtime_verification": "current_engine_only",
                    "note": "Retained OCI-runtime verification is not yet implemented.",
                }
                session.add(Resource(workspace=workspace, kind="replay", id=report_id, data=report))
                job.state, job.payload = (
                    "succeeded",
                    {**job.payload, "result": {"kind": "replay", "id": report_id}},
                )
                audit(session, workspace, self.owner, "run.replayed", payload["run_id"])
            return

        def checkpoint(**values):
            # Persist successful captures before the next reservation. A quota
            # pause after capture must never silently fetch a newer source.
            with self.db.sessions.begin() as session:
                current_job(session, claim)
                row = get_resource(session, workspace, "run", payload["run_id"])
                row.data = {**row.data, **values}

        snapshot_key, raw_key = run_data.get("snapshot_hash"), run_data.get("raw_hash")
        if source.navigation and (run_data.get("pages") or not snapshot_key):
            if not self.live_pages:
                raise PipelineError(
                    "CAPABILITY_UNSUPPORTED", "custom acquisition cannot navigate pages"
                )
            page_rows = list(run_data.get("pages", []))
            if not run_data.get("pages_complete"):
                retained = [
                    PageCapture(
                        page["url"],
                        self.store.get(page["raw_hash"]),
                        self.store.get(page["snapshot_hash"]),
                        retained=True,
                        request_url=page["request_url"],
                    )
                    for page in page_rows
                ]
                for page in acquire_pages(
                    source, retained=retained, reserve=self.db.reserve_origin
                ):
                    if page.retained:
                        continue
                    raw_hash = self.store.put(page.raw)
                    page_hash = self.store.put(page.snapshot)
                    page_rows.append(
                        {
                            "request_url": page.request_url,
                            "url": page.url,
                            "raw_hash": raw_hash,
                            "snapshot_hash": page_hash,
                        }
                    )
                    checkpoint(pages=page_rows)
                checkpoint(pages=page_rows, pages_complete=True)
            manifest = canonical(
                [{"url": page["url"], "snapshot_hash": page["snapshot_hash"]} for page in page_rows]
            )
            snapshot_key = self.store.put(manifest)
            checkpoint(snapshot_hash=snapshot_key)
            result = extract_pages(
                [(self.store.get(page["snapshot_hash"]), page["url"]) for page in page_rows],
                recipe,
                workspace_id=workspace,
            )
        else:
            if snapshot_key:
                snapshot = self.store.get(snapshot_key)
            else:
                if raw_key:
                    snapshot = normalize(self.store.get(raw_key), source.mode)
                else:
                    raw, snapshot = self.acquire(source)
                    raw_key = self.store.put(raw)
                    checkpoint(raw_hash=raw_key)
                snapshot_key = self.store.put(snapshot)
                checkpoint(snapshot_hash=snapshot_key)
            result = extract(snapshot, recipe, workspace, source.url)
        result_key = self.store.put(canonical(result.model_dump(mode="json")))
        previous = (
            ExtractionResult.model_validate_json(self.store.get(previous_key))
            if previous_key
            else None
        )
        diff = semantic_diff(previous.records if previous else [], result.records)
        checkpoint(
            result_key=result_key,
            counts={"records": len(result.records), "quarantined": len(result.quarantined)},
            diff=diff,
            expected_version=expected_version,
        )
        with self.db.sessions.begin() as session:
            job = current_job(session, claim)
            run = get_resource(session, workspace, "run", payload["run_id"])
            run.data = {
                **run.data,
                "snapshot_hash": snapshot_key,
                "raw_hash": raw_key,
                "result_key": result_key,
                "counts": {"records": len(result.records), "quarantined": len(result.quarantined)},
                "diff": diff,
                "expected_version": expected_version,
            }
            if result.quarantined or not result.records:
                run.data = {
                    **run.data,
                    "state": "failed",
                    "error": PipelineError(
                        "QUALITY_GATE_FAILED",
                        "invalid or empty records cannot be published; revise the recipe",
                    ).problem(),
                }
            elif diff["removed"]:
                review_id = uid()
                run.data = {**run.data, "state": "awaiting_review", "review_id": review_id}
                session.add(
                    Resource(
                        workspace=workspace,
                        kind="review",
                        id=review_id,
                        data={
                            "state": "open",
                            "run_id": run.id,
                            "reason": "UNEXPECTED_REMOVALS",
                            "diff": diff,
                        },
                    )
                )
                audit(session, workspace, self.owner, "review.opened", review_id)
            else:
                publish(session, workspace, run, result_key, expected_version, self.owner)
            job.state, job.payload = (
                "succeeded",
                {**job.payload, "result": {"kind": "run", "id": run.id}},
            )
            audit(
                session,
                workspace,
                self.owner,
                "run.finished",
                run.id,
                {"state": run.data["state"], "result_hash": result.result_hash},
            )

    def run_forever(self):
        while True:
            if not self.once():
                time.sleep(1)
