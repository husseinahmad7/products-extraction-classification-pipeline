from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from starlette.concurrency import run_in_threadpool

from product_pipeline.classification import rank_candidates
from product_pipeline.contracts import (
    Contract,
    ExtractionResult,
    Recipe,
    SourceSpec,
    TargetSchema,
    TaxonomySpec,
)
from product_pipeline.errors import PipelineError
from product_pipeline.hashing import object_hash

from .auth import authenticate, rotate
from .db import (
    Audit,
    Database,
    DatasetHead,
    Idempotency,
    Job,
    Resource,
    Schedule,
    StorageQuota,
    audit,
    database_now,
    get_resource,
    idempotent,
    uid,
)
from .middleware import BoundaryMiddleware
from .settings import Settings
from .worker import publish


class CompileRequest(Contract):
    source_id: str
    target_id: str
    snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    identity_fields: list[str] = Field(min_length=1)


class RecipeRequest(Contract):
    spec: Recipe
    snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RunRequest(Contract):
    recipe_id: str
    snapshot_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Decision(Contract):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=5, max_length=2000)


class Rotation(Contract):
    overlap_seconds: int = Field(default=300, ge=0, le=900)


class SuggestRequest(Contract):
    taxonomy_id: str
    text: str = Field(min_length=1, max_length=100000)


class QuotaExpansion(Contract):
    limit_bytes: int = Field(gt=0, le=2**63 - 1)
    reason: str = Field(min_length=5, max_length=2000)


class ScheduleRequest(Contract):
    recipe_id: str = Field(min_length=1, max_length=128)
    interval_seconds: int = Field(ge=60, le=2_592_000)


def representation(row: Resource):
    return {
        "id": row.id,
        "kind": row.kind,
        "version": row.version,
        "created": row.created.isoformat(),
        "data": row.data,
    }


def create_app(
    settings: Settings | None = None, database: Database | None = None, store=None
) -> FastAPI:
    settings = settings or Settings.from_env()
    db = database or Database(settings.database_url)
    artifacts = store or settings.store(db)
    workspace = settings.workspace
    app = FastAPI(
        title="Evidence Pipeline",
        version="0.1.0a1",
        description="Single-workspace alpha. Offline canonical replay and assisted extraction; see implementation status before deployment.",
    )
    app.state.database, app.state.store = db, artifacts
    app.add_middleware(BoundaryMiddleware, artifact_limit=settings.max_upload_bytes)

    @app.exception_handler(PipelineError)
    async def problem_handler(_request, exc: PipelineError):
        status = {
            "NOT_FOUND": 404,
            "UNAUTHORIZED": 401,
            "STALE_VERSION": 412,
            "IDEMPOTENCY_CONFLICT": 409,
            "CONFLICT": 409,
            "PUBLICATION_CONFLICT": 409,
            "QUOTA_EXCEEDED": 507,
        }.get(exc.code, 422)
        return JSONResponse(
            exc.problem(status),
            status_code=status,
            media_type="application/problem+json",
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )

    @app.exception_handler(StaleDataError)
    async def stale_handler(_request, _exc):
        return JSONResponse(
            PipelineError(
                "STALE_VERSION", "resource changed concurrently; refresh and retry"
            ).problem(412),
            status_code=412,
            media_type="application/problem+json",
        )

    def actor(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 512:
            raise PipelineError("UNAUTHORIZED", "administrator bearer token required")
        with db.sessions() as session:
            return authenticate(session, authorization[7:])

    def mutate(key, action, body, produce):
        if not key or len(key) > 128:
            raise PipelineError(
                "IDEMPOTENCY_REQUIRED", "Idempotency-Key of 1–128 characters is required"
            )
        payload = {"action": action, "body": body}
        try:
            with db.sessions.begin() as session:
                return idempotent(session, workspace, key, payload, lambda: produce(session))
        except IntegrityError as exc:
            with db.sessions() as session:
                existing = session.get(Idempotency, (workspace, key))
                if existing and existing.request_hash == object_hash(payload):
                    return existing.response
            raise PipelineError(
                "CONFLICT", "resource already exists or concurrent mutation conflicted"
            ) from exc

    def expect_version(row, value):
        if value != f'"{row.version}"':
            raise PipelineError("STALE_VERSION", "refresh resource and send its ETag in If-Match")

    def add_resource(session, kind, value, who):
        data = value.model_dump(mode="json")
        session.add(Resource(workspace=workspace, kind=kind, id=value.id, data=data))
        if kind == "source":
            session.add(DatasetHead(workspace=workspace, source_id=value.id, version=0))
        audit(session, workspace, who, kind + ".created", value.id)
        return {"id": value.id, "kind": kind}

    def operation(session, kind, payload, who):
        job_id = uid()
        current = database_now(session)
        session.add(
            Job(
                id=job_id,
                workspace=workspace,
                kind=kind,
                payload=payload,
                available=current,
                created=current,
            )
        )
        audit(session, workspace, who, "operation.queued", job_id, {"kind": kind})
        return {"id": job_id, "state": "queued", "location": f"/v1/operations/{job_id}"}

    def accepted(result):
        return JSONResponse(result, status_code=202, headers={"Location": result["location"]})

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with db.sessions() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "workspace": workspace}

    @app.get("/v1/capabilities")
    def capabilities(_who=Depends(actor)):
        return {
            "acquisition": {"live": ["html", "json"], "captured_only": ["browser", "pdf"]},
            "navigation": ["html_next", "html_links", "json_next", "json_links"],
            "scheduler": True,
            "classification": {"state": "not_certified", "automatic_promotion": False},
        }

    @app.post("/v1/sources", status_code=201)
    def source_create(
        body: SourceSpec, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        return mutate(
            idempotency_key,
            "source.create",
            body.model_dump(),
            lambda s: add_resource(s, "source", body, who),
        )

    @app.post("/v1/schemas", status_code=201)
    def schema_create(
        body: TargetSchema, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        return mutate(
            idempotency_key,
            "schema.create",
            body.model_dump(),
            lambda s: add_resource(s, "schema", body, who),
        )

    @app.post("/v1/taxonomies", status_code=201)
    def taxonomy_create(
        body: TaxonomySpec, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        return mutate(
            idempotency_key,
            "taxonomy.create",
            body.model_dump(),
            lambda s: add_resource(s, "taxonomy", body, who),
        )

    @app.put("/v1/artifacts")
    async def upload(request: Request, who=Depends(actor)):
        if request.headers.get("content-type", "").split(";")[0] not in {
            "text/html",
            "application/json",
            "text/plain",
        }:
            raise PipelineError(
                "MIME_UNSUPPORTED", "upload a UTF-8 HTML/JSON canonical artifact, not binary models"
            )
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > settings.max_upload_bytes:
                raise PipelineError("LIMIT_REACHED", "artifact upload exceeds limit")
        try:
            data.decode("utf-8")
        except UnicodeError as exc:
            raise PipelineError("ENCODING_UNSUPPORTED", "artifact must be UTF-8") from exc

        def retain():
            key = artifacts.put(bytes(data))
            with db.sessions.begin() as session:
                audit(session, workspace, who, "artifact.captured", key, {"bytes": len(data)})
            return key

        key = await run_in_threadpool(retain)
        return {"hash": key, "bytes": len(data)}

    @app.post("/v1/compile")
    def compile_create(
        body: CompileRequest, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        def create(session):
            get_resource(session, workspace, "source", body.source_id)
            get_resource(session, workspace, "schema", body.target_id)
            artifacts.get(body.snapshot_hash)
            return operation(session, "compile", body.model_dump(), who)

        return accepted(mutate(idempotency_key, "compile", body.model_dump(), create))

    @app.post("/v1/recipes")
    def recipe_create(
        body: RecipeRequest, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        def create(session):
            source = SourceSpec.model_validate(
                get_resource(session, workspace, "source", body.spec.source_id).data
            )
            if source.mode != body.spec.mode:
                raise PipelineError("MODE_MISMATCH", "recipe mode differs from source")
            artifacts.get(body.snapshot_hash)
            recipe_id = uid()
            session.add(
                Resource(
                    workspace=workspace,
                    kind="recipe",
                    id=recipe_id,
                    data={"spec": body.spec.model_dump(mode="json"), "state": "validating"},
                )
            )
            return operation(
                session,
                "validate",
                {"recipe_id": recipe_id, "snapshot_hash": body.snapshot_hash},
                who,
            )

        return accepted(mutate(idempotency_key, "recipe.create", body.model_dump(), create))

    @app.post("/v1/recipes/{recipe_id}/activate")
    def activate(
        recipe_id: str,
        body: Decision,
        who=Depends(actor),
        if_match: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        def apply(session):
            row = get_resource(session, workspace, "recipe", recipe_id)
            expect_version(row, if_match)
            if row.data["state"] != "review_required" or not row.data.get("validation", {}).get(
                "valid"
            ):
                raise PipelineError(
                    "ACTIVATION_BLOCKED", "recipe must pass validation before a decision"
                )
            row.data = {**row.data, "state": "active" if body.decision == "approve" else "rejected"}
            row.version += 1
            if body.decision == "approve":
                source_id = row.data["spec"]["source_id"]
                active = session.get(Resource, (workspace, "active_recipe", source_id))
                if active:
                    previous = get_resource(session, workspace, "recipe", active.data["recipe_id"])
                    previous.data = {**previous.data, "state": "superseded"}
                    active.data, active.version = {"recipe_id": recipe_id}, active.version + 1
                else:
                    session.add(
                        Resource(
                            workspace=workspace,
                            kind="active_recipe",
                            id=source_id,
                            data={"recipe_id": recipe_id},
                        )
                    )
            audit(
                session,
                workspace,
                who,
                "recipe." + body.decision,
                recipe_id,
                {"reason": body.reason},
            )
            return {"id": recipe_id, "state": row.data["state"]}

        return mutate(
            idempotency_key,
            "activate:" + recipe_id,
            {**body.model_dump(), "if_match": if_match},
            apply,
        )

    @app.post("/v1/runs")
    def run_create(
        body: RunRequest, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        def create(session):
            recipe = get_resource(session, workspace, "recipe", body.recipe_id)
            source_id = recipe.data["spec"]["source_id"]
            active = session.get(Resource, (workspace, "active_recipe", source_id))
            if not active or active.data["recipe_id"] != body.recipe_id:
                raise PipelineError("RECIPE_NOT_ACTIVE", "approve this recipe before running")
            if body.snapshot_hash:
                artifacts.get(body.snapshot_hash)
            run_id = uid()
            session.add(
                Resource(
                    workspace=workspace,
                    kind="run",
                    id=run_id,
                    data={
                        "source_id": source_id,
                        "recipe_id": body.recipe_id,
                        "snapshot_hash": body.snapshot_hash,
                        "state": "queued",
                        "classification": "NOT_CONFIGURED",
                    },
                )
            )
            return {**operation(session, "run", {"run_id": run_id}, who), "run_id": run_id}

        return accepted(mutate(idempotency_key, "run.create", body.model_dump(), create))

    def schedule_view(row: Schedule):
        return {
            "id": row.id,
            "source_id": row.source_id,
            "recipe_id": row.recipe_id,
            "interval_seconds": row.interval_seconds,
            "next_due": row.next_due.isoformat() if row.next_due else None,
            "state": row.state,
            "version": row.version,
            "last_run_id": row.last_run_id,
            "created": row.created.isoformat() if row.created else None,
        }

    @app.post("/v1/schedules", status_code=201)
    def schedule_create(
        body: ScheduleRequest,
        who=Depends(actor),
        idempotency_key: str | None = Header(default=None),
    ):
        def create(session):
            recipe = get_resource(session, workspace, "recipe", body.recipe_id)
            source_id = recipe.data["spec"]["source_id"]
            active = session.get(Resource, (workspace, "active_recipe", source_id))
            if active is None or active.data.get("recipe_id") != body.recipe_id:
                raise PipelineError("RECIPE_NOT_ACTIVE", "approve this recipe before scheduling")
            source = SourceSpec.model_validate(
                get_resource(session, workspace, "source", source_id).data
            )
            if source.mode not in {"html", "json"}:
                raise PipelineError(
                    "CAPABILITY_UNSUPPORTED", "scheduled acquisition requires HTML or JSON"
                )
            schedule_id = uid()
            row = Schedule(
                id=schedule_id,
                workspace=workspace,
                source_id=source_id,
                recipe_id=body.recipe_id,
                interval_seconds=body.interval_seconds,
                next_due=database_now(session) + timedelta(seconds=body.interval_seconds),
                state="active",
                version=1,
                created=database_now(session),
            )
            session.add(row)
            audit(session, workspace, who, "schedule.created", schedule_id)
            return schedule_view(row)

        return mutate(idempotency_key, "schedule.create", body.model_dump(), create)

    @app.get("/v1/schedules")
    def schedule_list(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str = Query(default="", max_length=36),
        _who=Depends(actor),
    ):
        with db.sessions() as session:
            rows = session.scalars(
                select(Schedule)
                .where(Schedule.workspace == workspace, Schedule.id > cursor)
                .order_by(Schedule.id)
                .limit(limit + 1)
            ).all()
            return {
                "items": [schedule_view(row) for row in rows[:limit]],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    @app.get("/v1/schedules/{schedule_id}")
    def schedule_get(schedule_id: str, _who=Depends(actor)):
        with db.sessions() as session:
            row = session.get(Schedule, schedule_id)
            if row is None or row.workspace != workspace:
                raise PipelineError("NOT_FOUND", "schedule not found")
            return JSONResponse(schedule_view(row), headers={"ETag": f'"{row.version}"'})

    @app.post("/v1/schedules/{schedule_id}/{action}")
    def schedule_change(
        schedule_id: str,
        action: Literal["pause", "resume"],
        who=Depends(actor),
        if_match: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        def change(session):
            row = session.scalar(
                select(Schedule)
                .where(Schedule.id == schedule_id, Schedule.workspace == workspace)
                .with_for_update()
            )
            if row is None:
                raise PipelineError("NOT_FOUND", "schedule not found")
            if if_match != f'"{row.version}"':
                raise PipelineError(
                    "STALE_VERSION", "refresh schedule and send its ETag in If-Match"
                )
            if action == "resume":
                active = session.get(Resource, (workspace, "active_recipe", row.source_id))
                if active is None or active.data.get("recipe_id") != row.recipe_id:
                    raise PipelineError("RECIPE_NOT_ACTIVE", "approve this recipe before resuming")
                row.next_due = database_now(session) + timedelta(seconds=row.interval_seconds)
            row.state = "paused" if action == "pause" else "active"
            row.version += 1
            audit(session, workspace, who, "schedule." + action, schedule_id)
            return schedule_view(row)

        return mutate(
            idempotency_key,
            "schedule." + action + ":" + schedule_id,
            {"if_match": if_match},
            change,
        )

    @app.get("/v1/operations/{operation_id}")
    def operation_get(operation_id: str, _who=Depends(actor)):
        with db.sessions() as session:
            row = session.get(Job, operation_id)
            if row is None or row.workspace != workspace:
                raise PipelineError("NOT_FOUND", "operation not found")
            return {
                "id": row.id,
                "kind": row.kind,
                "state": row.state,
                "attempts": row.attempts,
                "generation": row.generation,
                "created": row.created,
                "lease_until": row.lease_until,
                "result": row.payload.get("result"),
                "error": row.error,
            }

    @app.get("/v1/operations")
    def operation_list(
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=36),
        _who=Depends(actor),
    ):
        with db.sessions() as session:
            query = select(Job).where(Job.workspace == workspace)
            if cursor:
                previous = session.get(Job, cursor)
                if previous is None or previous.workspace != workspace:
                    raise PipelineError("NOT_FOUND", "operation cursor not found")
                query = query.where(
                    or_(
                        Job.created < previous.created,
                        and_(Job.created == previous.created, Job.id < previous.id),
                    )
                )
            rows = session.scalars(
                query.order_by(Job.created.desc(), Job.id.desc()).limit(limit + 1)
            ).all()
            items = [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "state": row.state,
                    "attempts": row.attempts,
                    "generation": row.generation,
                    "created": row.created,
                    "lease_until": row.lease_until,
                    "result": row.payload.get("result"),
                    "error": row.error,
                }
                for row in rows[:limit]
            ]
            return {
                "items": items,
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    @app.post("/v1/operations/{operation_id}/cancel")
    def cancel(
        operation_id: str, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        def apply(session):
            row = session.scalar(
                select(Job)
                .where(Job.id == operation_id, Job.workspace == workspace)
                .with_for_update()
            )
            if row is None:
                raise PipelineError("NOT_FOUND", "operation not found")
            if row.state in {"queued", "running", "paused"}:
                row.state = "cancelled"
                if row.kind == "run":
                    run = get_resource(session, workspace, "run", row.payload["run_id"])
                    run.data, run.version = {**run.data, "state": "cancelled"}, run.version + 1
                audit(session, workspace, who, "operation.cancelled", operation_id)
            return {"id": row.id, "state": row.state}

        return mutate(idempotency_key, "cancel:" + operation_id, {}, apply)

    @app.post("/v1/operations/{operation_id}/resume")
    def resume(
        operation_id: str, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        def apply(session):
            row = session.scalar(
                select(Job)
                .where(Job.id == operation_id, Job.workspace == workspace)
                .with_for_update()
            )
            if row is None:
                raise PipelineError("NOT_FOUND", "operation not found")
            if row.state != "paused":
                raise PipelineError("CONFLICT", "only a paused operation can be resumed")
            quota = session.get(StorageQuota, workspace)
            if quota is None or quota.used_bytes >= quota.limit_bytes:
                raise PipelineError("QUOTA_EXCEEDED", "expand capacity before resuming")
            row.state, row.error, row.available = "queued", None, database_now(session)
            if row.kind == "run":
                run = get_resource(session, workspace, "run", row.payload["run_id"])
                run.data = {**run.data, "state": "queued", "error": None}
            audit(session, workspace, who, "operation.resumed", operation_id)
            return {"id": row.id, "state": row.state, "location": f"/v1/operations/{row.id}"}

        return accepted(mutate(idempotency_key, "resume:" + operation_id, {}, apply))

    @app.get("/v1/storage")
    def storage_get(_who=Depends(actor)):
        with db.sessions() as session:
            quota = session.get(StorageQuota, workspace)
            if quota is None:
                raise PipelineError("NOT_FOUND", "quota store is not configured")
            return {"limit_bytes": quota.limit_bytes, "used_bytes": quota.used_bytes}

    @app.post("/v1/storage/expand")
    def storage_expand(
        body: QuotaExpansion, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        def apply(session):
            quota = session.scalar(
                select(StorageQuota).where(StorageQuota.workspace == workspace).with_for_update()
            )
            if quota is None:
                raise PipelineError("NOT_FOUND", "quota store is not configured")
            if body.limit_bytes <= quota.limit_bytes:
                raise PipelineError(
                    "CONFLICT", "capacity may only be expanded; evidence is never evicted"
                )
            previous = quota.limit_bytes
            quota.limit_bytes = body.limit_bytes
            audit(
                session,
                workspace,
                who,
                "storage.expanded",
                workspace,
                {
                    "previous_bytes": previous,
                    "limit_bytes": body.limit_bytes,
                    "reason": body.reason,
                },
            )
            return {"limit_bytes": quota.limit_bytes, "used_bytes": quota.used_bytes}

        return mutate(idempotency_key, "storage.expand", body.model_dump(), apply)

    @app.post("/v1/runs/{run_id}/replay")
    def replay_create(
        run_id: str, who=Depends(actor), idempotency_key: str | None = Header(default=None)
    ):
        def create(session):
            get_resource(session, workspace, "run", run_id)
            return operation(session, "replay", {"run_id": run_id}, who)

        return accepted(mutate(idempotency_key, "replay:" + run_id, {}, create))

    @app.get("/v1/runs/{run_id}/records")
    def records(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
        _who=Depends(actor),
    ):
        with db.sessions() as session:
            row = get_resource(session, workspace, "run", run_id)
            key = row.data.get("result_key")
        if not key:
            return {"items": [], "quarantined": [], "total": 0}
        result = ExtractionResult.model_validate_json(artifacts.get(key))
        return {
            "items": [r.model_dump() for r in result.records[offset : offset + limit]],
            "quarantined": [r.model_dump() for r in result.quarantined[offset : offset + limit]],
            "total": len(result.records),
            "result_hash": result.result_hash,
        }

    @app.get("/v1/revisions/{revision_id}/export")
    def export(revision_id: str, _who=Depends(actor)):
        with db.sessions() as session:
            row = get_resource(session, workspace, "revision", revision_id)
        result = ExtractionResult.model_validate_json(artifacts.get(row.data["result_key"]))
        payload = (
            "\n".join(
                json.dumps(r.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
                for r in result.records
            )
            + "\n"
        )
        return Response(
            payload,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{revision_id}.jsonl"'},
        )

    @app.post("/v1/reviews/{review_id}/decision")
    def review_decide(
        review_id: str,
        body: Decision,
        who=Depends(actor),
        if_match: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        def apply(session):
            row = get_resource(session, workspace, "review", review_id)
            expect_version(row, if_match)
            if row.data["state"] != "open":
                raise PipelineError("CONFLICT", "review is already resolved")
            run = get_resource(session, workspace, "run", row.data["run_id"])
            if run.data["state"] != "awaiting_review":
                raise PipelineError("CONFLICT", "run is not awaiting this review")
            if body.decision == "approve":
                publish(
                    session,
                    workspace,
                    run,
                    run.data["result_key"],
                    run.data["expected_version"],
                    who,
                )
            else:
                run.data = {
                    **run.data,
                    "state": "failed",
                    "error": PipelineError("REVIEW_REJECTED", body.reason).problem(),
                }
                run.version += 1
            row.data, row.version = (
                {
                    **row.data,
                    "state": "approved" if body.decision == "approve" else "rejected",
                    "reason": body.reason,
                    "actor": who,
                },
                row.version + 1,
            )
            audit(
                session,
                workspace,
                who,
                "review." + body.decision,
                review_id,
                {"reason": body.reason},
            )
            return {"id": review_id, "state": row.data["state"]}

        return mutate(
            idempotency_key,
            "review:" + review_id,
            {**body.model_dump(), "if_match": if_match},
            apply,
        )

    @app.post("/v1/classification/suggest")
    def suggest(body: SuggestRequest, _who=Depends(actor)):
        with db.sessions() as session:
            taxonomy = TaxonomySpec.model_validate(
                get_resource(session, workspace, "taxonomy", body.taxonomy_id).data
            )
        return rank_candidates(body.text, taxonomy)

    @app.post("/v1/admin-tokens/rotate")
    def token_rotate(body: Rotation, who=Depends(actor)):
        # Never persist plaintext tokens in the idempotency response table.
        with db.sessions.begin() as session:
            token = rotate(session, workspace, who, body.overlap_seconds)
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})

    @app.get("/v1/audit")
    def audit_list(
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
        _who=Depends(actor),
    ):
        with db.sessions() as session:
            rows = session.scalars(
                select(Audit)
                .where(Audit.workspace == workspace, Audit.sequence > after)
                .order_by(Audit.sequence)
                .limit(limit + 1)
            ).all()
            items = [
                {
                    "sequence": r.sequence,
                    "actor": r.actor,
                    "action": r.action,
                    "target": r.target,
                    "details": r.details,
                    "created": r.created.isoformat(),
                }
                for r in rows[:limit]
            ]
            return {
                "items": items,
                "next_cursor": items[-1]["sequence"] if len(rows) > limit else None,
            }

    collections = {
        "sources": "source",
        "schemas": "schema",
        "taxonomies": "taxonomy",
        "recipes": "recipe",
        "drafts": "draft",
        "runs": "run",
        "reviews": "review",
        "revisions": "revision",
        "replays": "replay",
    }

    @app.get("/v1/{collection}")
    def list_resources(
        collection: str,
        cursor: str = "",
        limit: int = Query(default=50, ge=1, le=200),
        _who=Depends(actor),
    ):
        if collection not in collections:
            raise PipelineError("NOT_FOUND", "collection not found")
        with db.sessions() as session:
            rows = session.scalars(
                select(Resource)
                .where(
                    Resource.workspace == workspace,
                    Resource.kind == collections[collection],
                    Resource.id > cursor,
                )
                .order_by(Resource.id)
                .limit(limit + 1)
            ).all()
            return {
                "items": [representation(r) for r in rows[:limit]],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    @app.get("/v1/{collection}/{resource_id}")
    def resource_get(collection: str, resource_id: str, _who=Depends(actor)):
        if collection not in collections:
            raise PipelineError("NOT_FOUND", "collection not found")
        with db.sessions() as session:
            row = get_resource(session, workspace, collections[collection], resource_id)
            return JSONResponse(representation(row), headers={"ETag": f'"{row.version}"'})

    if settings.dashboard_dir:
        app.mount("/", StaticFiles(directory=settings.dashboard_dir, html=True), name="dashboard")
    return app
