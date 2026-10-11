"""Conservative reservations, not estimates of provider billing. PostgreSQL serializes admission."""

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from product_pipeline.contracts import SourceSpec
from product_pipeline.errors import PipelineError

from .db import ApprovedDomain, BudgetUsage, current_job, get_resource, now

GLOBAL = "__global__"


def normalize_domain(value: str) -> str:
    try:
        host = value.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise PipelineError("SOURCE_DENIED", "invalid public hostname") from exc
    if len(host) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in host.split(".")
    ):
        raise PipelineError("SOURCE_DENIED", "an exact public hostname is required")
    if "." not in host or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid")):
        raise PipelineError("SOURCE_DENIED", "private or local hostnames are unsupported")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise PipelineError("SOURCE_DENIED", "literal IP addresses are unsupported")


def validate_source(session, source: SourceSpec, settings):
    if settings.auth_mode != "neon":
        return
    parsed = urlsplit(source.url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise PipelineError("SOURCE_DENIED", "invalid source port") from exc
    if parsed.scheme != "https" or port not in {None, 443} or source.auth_ref:
        raise PipelineError(
            "SOURCE_DENIED",
            "beta supports public HTTPS sources without server credential references",
        )
    if source.mode not in {"html", "json"}:
        raise PipelineError("SOURCE_DENIED", "live beta sources must be HTML or JSON")
    for domain in source.allowed_domains:
        approved = session.get(ApprovedDomain, normalize_domain(domain))
        if approved is None or not approved.enabled:
            raise PipelineError(
                "SOURCE_DENIED", "request operator approval for every source hostname"
            )
    caps = {
        "max_pages": 10,
        "max_records": 1000,
        "max_bytes": 1_000_000,
        "deadline_seconds": 60,
        "request_timeout_seconds": 20,
    }
    if (
        any(getattr(source.limits, key) > cap for key, cap in caps.items())
        or source.min_interval_seconds < 1
    ):
        raise PipelineError(
            "LIMIT_REACHED",
            "beta source limits: 10 pages, 1000 records, 1MB, 60s; minimum interval 1s",
        )


def limits(settings, global_scope=False):
    return {
        "api_requests": settings.beta_global_max_api_requests
        if global_scope
        else settings.beta_max_api_requests,
        "jobs": settings.beta_global_max_jobs if global_scope else settings.beta_max_jobs,
        "pages": 1000 if global_scope else 100,
        "source_bytes": 100_000_000 if global_scope else 10_000_000,
        "runtime_seconds": settings.beta_global_max_runtime_seconds if global_scope else 1800,
        "metadata_bytes": settings.beta_global_metadata_quota_bytes
        if global_scope
        else settings.beta_metadata_quota_bytes,
        "artifact_read_bytes": 2_000_000_000 if global_scope else 200_000_000,
    }


def reserve(session, workspace, amounts, settings):
    monthly = {key: value for key, value in amounts.items() if key != "metadata_bytes"}
    periods = [(now().strftime("%Y-%m"), monthly)] if monthly else []
    if "metadata_bytes" in amounts:
        # Database metadata is retained; its storage budget must never reset monthly.
        periods.append(("total", {"metadata_bytes": amounts["metadata_bytes"]}))
    # Global first everywhere: stable row-lock order avoids inter-tenant deadlocks.
    for scope in (GLOBAL, workspace):
        for period, charges in periods:
            values: dict[str, Any] = {
                "scope": scope,
                "period": period,
                "counters": {},
                "version": 1,
            }
            dialect = session.get_bind().dialect.name
            if dialect in {"postgresql", "sqlite"}:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                insert = pg_insert if dialect == "postgresql" else sqlite_insert
                session.execute(insert(BudgetUsage).values(**values).on_conflict_do_nothing())
            else:
                raise PipelineError(
                    "CAPABILITY_UNSUPPORTED", "budget admission requires PostgreSQL or SQLite"
                )
            row = session.scalar(
                select(BudgetUsage)
                .where(BudgetUsage.scope == scope, BudgetUsage.period == period)
                .with_for_update()
            )
            caps = limits(settings, scope == GLOBAL)
            updated = {**row.counters}
            for name, amount in charges.items():
                if amount < 0 or name not in caps:
                    raise ValueError("invalid budget reservation")
                updated[name] = updated.get(name, 0) + amount
                if updated[name] > caps[name]:
                    raise PipelineError(
                        "QUOTA_EXCEEDED",
                        f"{name} budget reached; ingestion is paused, evidence retained",
                    )
            row.counters = updated
            session.flush()


def reserve_api(session, workspace, settings, metadata_bytes=0):
    reserve(session, workspace, {"api_requests": 1, "metadata_bytes": metadata_bytes}, settings)


def reserve_operation(session, workspace, settings):
    if settings.auth_mode == "neon":
        reserve(session, workspace, {"jobs": 1}, settings)


def guard_claim(database, claim, settings):
    if settings.auth_mode != "neon":
        return
    with database.sessions.begin() as session:
        job = current_job(session, claim)
        amounts = {"runtime_seconds": 60}
        if claim["kind"] == "run":
            run = get_resource(session, claim["workspace"], "run", claim["payload"]["run_id"])
            recipe = get_resource(session, claim["workspace"], "recipe", run.data["recipe_id"])
            source = SourceSpec.model_validate(
                get_resource(
                    session, claim["workspace"], "source", recipe.data["spec"]["source_id"]
                ).data
            )
            needs_live = not run.data.get("snapshot_hash") or (
                source.navigation and not run.data.get("pages_complete")
            )
            if needs_live:
                validate_source(session, source, settings)
                amounts.update(pages=source.limits.max_pages, source_bytes=source.limits.max_bytes)
            if run.data.get("schedule_id") and not job.payload.get("beta_jobs_reserved"):
                reserve_operation(session, claim["workspace"], settings)
                job.payload = {**job.payload, "beta_jobs_reserved": True}
        if not job.payload.get("beta_budget_reserved"):
            reserve(session, claim["workspace"], amounts, settings)
            job.payload = {**job.payload, "beta_budget_reserved": True}


def admit_schedule(session, schedule, settings):
    """Reserve public scheduled work before any run/job row is created.

    The scheduler supplies a savepoint, so a tenant denial after a global charge
    cannot leak reservations. Existing claim flags remain the sole durable
    idempotency marker; the live-source guard still revalidates at execution.
    """
    if settings.auth_mode != "neon":
        return {}
    if schedule.interval_seconds < 900:
        raise PipelineError("LIMIT_REACHED", "beta schedules must be at least 15 minutes apart")
    source = SourceSpec.model_validate(
        get_resource(session, schedule.workspace, "source", schedule.source_id).data
    )
    validate_source(session, source, settings)
    reserve_operation(session, schedule.workspace, settings)
    reserve(
        session,
        schedule.workspace,
        {
            "runtime_seconds": 60,
            "pages": source.limits.max_pages,
            "source_bytes": source.limits.max_bytes,
            "metadata_bytes": 16_384,
        },
        settings,
    )
    return {"beta_jobs_reserved": True, "beta_budget_reserved": True}
