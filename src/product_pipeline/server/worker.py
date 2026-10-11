"""Worker orchestration. Artifact writes stage immutable bytes; only fenced DB commits publish."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass(frozen=True)
class BatchReport:
    processed: int
    reason: str
    elapsed_seconds: float


@dataclass(frozen=True)
class _BatchBudget:
    deadline: float

    def check(self, minimum_seconds: float = 0):
        if self.deadline - time.monotonic() <= minimum_seconds:
            raise PipelineError(
                "BATCH_DEADLINE",
                "batch time budget reached; retained evidence will be reused next dispatch",
                retryable=True,
            )


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
    def __init__(
        self,
        database: Database,
        store=None,
        owner: str | None = None,
        acquire_fn=acquire,
        *,
        store_factory: Callable | None = None,
        workspace: str | None = None,
        claim_guard: Callable | None = None,
        schedule_admission: Callable | None = None,
    ):
        if (store is None) == (store_factory is None):
            raise ValueError("supply exactly one store or workspace store factory")
        self.db, self.store = database, store
        self.store_factory, self.claim_guard = store_factory, claim_guard
        self.schedule_admission = schedule_admission
        stored_workspace = getattr(store, "workspace", None)
        if stored_workspace is not None and workspace not in {None, stored_workspace}:
            raise ValueError("worker workspace differs from its quota store")
        self.workspace = workspace if workspace is not None else stored_workspace
        self._budget: _BatchBudget | None = None
        self._batch_yielded = False
        self._acquisition_budget_limited = False
        self.owner = owner or uid()
        self.live_pages = acquire_fn is acquire
        if self.live_pages:

            def acquire_one(source):
                source = self.bounded_source(source)
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

    def check_budget(self, minimum_seconds: float = 0):
        if self._budget is not None:
            self._budget.check(minimum_seconds)

    def bounded_source(self, source: SourceSpec) -> SourceSpec:
        """Clamp live HTTP work to the remaining cooperative batch budget."""
        if self._budget is None:
            return source
        self.check_budget(minimum_seconds=1)
        seconds = max(1, int(self._budget.deadline - time.monotonic()))
        if seconds < source.limits.deadline_seconds:
            self._acquisition_budget_limited = True
        limits = source.limits.model_copy(
            update={
                "deadline_seconds": min(source.limits.deadline_seconds, seconds),
                "request_timeout_seconds": min(source.limits.request_timeout_seconds, seconds),
            }
        )
        return source.model_copy(update={"limits": limits})

    def once(self, workspace: str | None = None) -> bool:
        if self.workspace is not None and workspace not in {None, self.workspace}:
            raise ValueError("a scoped worker cannot claim another workspace")
        workspace = workspace if workspace is not None else self.workspace
        self._batch_yielded = False
        self._acquisition_budget_limited = False
        self.db.dispatch_due_schedules(workspace=workspace, admission=self.schedule_admission)
        claim = self.db.claim(
            self.owner, kinds=("run", "compile", "validate", "replay"), workspace=workspace
        )
        if claim is None:
            return False
        try:
            with self.heartbeats(claim):
                self.check_budget()
                if self.claim_guard is not None:
                    self.claim_guard(claim)
                if self.store_factory is not None:
                    self.store = self.store_factory(claim["workspace"])
                    if getattr(self.store, "workspace", claim["workspace"]) != claim["workspace"]:
                        raise PipelineError("STATE_CORRUPT", "claim store workspace does not match")
                self.execute(claim)
        except PipelineError as exc:
            # acquire_pages has its own deadline. A shorter batch-imposed
            # deadline is a cooperative yield, not an invalid source or a
            # worker-crash retry. Other limits remain permanent failures.
            if (
                self._budget is not None
                and self._acquisition_budget_limited
                and (
                    (exc.code == "LIMIT_REACHED" and "deadline" in exc.detail)
                    or (exc.code == "FETCH_FAILED" and time.monotonic() >= self._budget.deadline)
                )
            ):
                exc = PipelineError(
                    "BATCH_DEADLINE", "batch acquisition budget reached", retryable=True
                )
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
                yielded = error.code == "BATCH_DEADLINE"
                self._batch_yielded = yielded
                retry = not paused and (yielded or (error.retryable and job.attempts < 3))
                job.state = "paused" if paused else "queued" if retry else "failed"
                if paused or yielded:
                    # Capacity waits and cooperative yields are not crashes.
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
                    "job.paused"
                    if paused
                    else "job.yielded"
                    if yielded
                    else "job.retry"
                    if retry
                    else "job.failed",
                    job.id,
                    {"code": error.code, "generation": job.generation},
                )
        except PipelineError as exc:
            if exc.code != "LEASE_LOST":
                raise

    def execute(self, claim):
        self.check_budget()
        workspace, payload = claim["workspace"], claim["payload"]
        with self.db.sessions.begin() as session:
            job = current_job(session, claim)
            job.error = None
            if claim["kind"] == "run":
                run = get_resource(session, workspace, "run", payload["run_id"])
                run.data = {
                    **{key: value for key, value in run.data.items() if key != "error"},
                    "state": "running",
                }
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
            self.check_budget()
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
            self.check_budget()
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
            self.check_budget()
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
                captures = iter(
                    acquire_pages(
                        self.bounded_source(source),
                        retained=retained,
                        reserve=self.db.reserve_origin,
                    )
                )
                while True:
                    self.check_budget()
                    try:
                        page = next(captures)
                    except StopIteration:
                        break
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
            self.check_budget()
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
                    raw, snapshot = self.acquire(self.bounded_source(source))
                    raw_key = self.store.put(raw)
                    checkpoint(raw_hash=raw_key)
                snapshot_key = self.store.put(snapshot)
                checkpoint(snapshot_hash=snapshot_key)
            self.check_budget()
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
        self.check_budget()
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

    def run_batch(self, max_jobs: int = 3, max_seconds: float = 120) -> BatchReport:
        """Exit on first idle query, job limit, or cooperative deadline.

        The deadline is checked between processing stages/pages and bounds
        HTTP acquisition time. It cannot interrupt a blocked DB/S3 call or
        CPU operation. A host hard timeout remains necessary; expired fenced
        leases recover interrupted work and reuse retained captures.
        """
        if max_jobs < 1 or max_seconds <= 0 or not math.isfinite(max_seconds):
            raise ValueError("batch job and time budgets must be positive")
        if self._budget is not None:
            raise ValueError("worker batch is already running")
        started = time.monotonic()
        self._budget = _BatchBudget(started + max_seconds)
        processed = 0
        reason = "job_limit"
        try:
            while processed < max_jobs:
                if time.monotonic() >= self._budget.deadline:
                    reason = "deadline"
                    break
                if not self.once():
                    reason = "idle"
                    break
                processed += 1
                if self._batch_yielded or time.monotonic() >= self._budget.deadline:
                    reason = "deadline"
                    break
            return BatchReport(processed, reason, round(time.monotonic() - started, 6))
        finally:
            self._budget = None

    def run_forever(self):
        """Explicit legacy/self-hosted mode; do not use on free-tier beta."""
        while True:
            if not self.once():
                time.sleep(1)
