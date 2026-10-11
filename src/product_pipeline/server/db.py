"""Durable jobs with generation fencing; PostgreSQL is the deployment database."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from product_pipeline.errors import PipelineError
from product_pipeline.hashing import object_hash


def now() -> datetime:
    # Persist UTC without ambiguous per-driver timezone conversions.
    return datetime.now(UTC).replace(tzinfo=None)


def database_now(session) -> datetime:
    if session.bind.dialect.name == "postgresql":
        return session.scalar(select(func.clock_timestamp())).astimezone(UTC).replace(tzinfo=None)
    return now()


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Resource(Base):
    __tablename__ = "resources"
    workspace: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    __mapper_args__ = {"version_id_col": version}


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    available: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    created: Mapped[datetime] = mapped_column(
        DateTime, default=now, server_default="CURRENT_TIMESTAMP"
    )
    error: Mapped[dict | None] = mapped_column(JSON)


class Idempotency(Base):
    __tablename__ = "idempotency"
    workspace: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)


class Audit(Base):
    __tablename__ = "audit"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace: Mapped[str] = mapped_column(String(128), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace: Mapped[str] = mapped_column(String(128), index=True)
    event: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)


class DatasetHead(Base):
    __tablename__ = "dataset_heads"
    workspace: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=0)


class AdminToken(Base):
    __tablename__ = "admin_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    secret_hash: Mapped[str] = mapped_column(String(512))
    expires: Mapped[datetime | None] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (UniqueConstraint("secret_hash"),)


class StorageQuota(Base):
    __tablename__ = "storage_quotas"
    workspace: Mapped[str] = mapped_column(String(128), primary_key=True)
    limit_bytes: Mapped[int] = mapped_column(BigInteger)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class Artifact(Base):
    __tablename__ = "artifacts"
    workspace: Mapped[str] = mapped_column(String(128), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20))


class OriginThrottle(Base):
    __tablename__ = "origin_throttles"
    origin: Mapped[str] = mapped_column(String(512), primary_key=True)
    next_allowed: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128))
    recipe_id: Mapped[str] = mapped_column(String(128))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_due: Mapped[datetime] = mapped_column(DateTime, index=True)
    state: Mapped[str] = mapped_column(String(20), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_run_id: Mapped[str | None] = mapped_column(String(36))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserWorkspace(Base):
    __tablename__ = "user_workspaces"
    workspace: Mapped[str] = mapped_column(String(128), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(512))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("issuer", "subject"),)


class ApprovedDomain(Base):
    __tablename__ = "approved_domains"
    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(2000))
    created: Mapped[datetime] = mapped_column(DateTime, default=now)


class DomainRequest(Base):
    __tablename__ = "domain_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace: Mapped[str] = mapped_column(String(128), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    reason: Mapped[str] = mapped_column(String(2000))
    state: Mapped[str] = mapped_column(String(20), default="pending")
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    decided_by: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created: Mapped[datetime] = mapped_column(DateTime, default=now)
    __mapper_args__ = {"version_id_col": version}


class BudgetUsage(Base):
    __tablename__ = "budget_usage"
    scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    counters: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": version}


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)

    def claim(
        self,
        owner: str,
        lease_seconds: int = 60,
        kinds: tuple[str, ...] = ("run", "compile", "replay"),
        workspace: str | None = None,
    ) -> dict | None:
        with self.sessions.begin() as session:
            current = database_now(session)
            query = (
                select(Job)
                .where(
                    Job.kind.in_(kinds),
                    *((Job.workspace == workspace,) if workspace is not None else ()),
                    Job.available <= current,
                    or_(
                        Job.state == "queued",
                        (Job.state == "running") & (Job.lease_until < current),
                    ),
                )
                .order_by(Job.available, Job.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = session.scalar(query)
            if job is None:
                return None
            if job.attempts >= 3:
                job.state = "failed"
                job.error = PipelineError(
                    "RETRIES_EXHAUSTED", "worker crashed repeatedly"
                ).problem()
                if job.kind == "run" and job.payload.get("run_id"):
                    run = get_resource(session, job.workspace, "run", job.payload["run_id"])
                    run.data = {**run.data, "state": "failed", "error": job.error}
                audit(session, job.workspace, owner, "job.dead_lettered", job.id)
                return None
            job.state, job.owner = "running", owner
            job.generation += 1
            job.attempts += 1
            job.lease_until = current + timedelta(seconds=lease_seconds)
            return {
                "id": job.id,
                "workspace": job.workspace,
                "kind": job.kind,
                "payload": job.payload,
                "generation": job.generation,
                "owner": owner,
            }

    def heartbeat(self, claim: dict, lease_seconds: int = 60) -> bool:
        with self.sessions.begin() as session:
            current = database_now(session)
            result = session.scalar(
                update(Job)
                .where(*fence(claim, current))
                .values(lease_until=current + timedelta(seconds=lease_seconds))
                .returning(Job.id)
            )
            return result is not None

    def reserve_origin(
        self, origin: str, interval_seconds: float, remaining_seconds: float
    ) -> float:
        """Reserve one request slot across workers using a database row lock."""
        if remaining_seconds <= 0:
            raise PipelineError("LIMIT_REACHED", "acquisition deadline reached")
        with self.sessions.begin() as session:
            initial = database_now(session)
            values = {"origin": origin, "next_allowed": initial}
            if session.get_bind().dialect.name == "postgresql":
                session.execute(pg_insert(OriginThrottle).values(**values).on_conflict_do_nothing())
            elif session.get_bind().dialect.name == "sqlite":
                session.execute(
                    sqlite_insert(OriginThrottle).values(**values).on_conflict_do_nothing()
                )
            else:
                raise PipelineError(
                    "CAPABILITY_UNSUPPORTED", "origin throttle requires PostgreSQL or SQLite"
                )
            row = session.scalar(
                select(OriginThrottle).where(OriginThrottle.origin == origin).with_for_update()
            )
            if row is None:
                raise PipelineError("STATE_CORRUPT", "origin throttle reservation failed")
            current = database_now(session)
            planned = max(current, row.next_allowed)
            wait = (planned - current).total_seconds()
            if wait >= remaining_seconds:
                raise PipelineError("LIMIT_REACHED", "origin throttle exceeds acquisition deadline")
            row.next_allowed = planned + timedelta(seconds=interval_seconds)
            return wait

    def dispatch_due_schedules(
        self,
        limit: int = 10,
        workspace: str | None = None,
        admission: Callable | None = None,
    ) -> int:
        """Coalesce due intervals only after transactional schedule admission.

        A trusted admission callback receives this session and the schedule,
        and returns reservation flags for the new job. Its writes, the schedule
        advance and run/job creation share a savepoint: denied work cannot leave
        partial budget charges or another queued operation behind.
        """
        dispatched = 0
        with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                # sqlite3's legacy transaction mode does not BEGIN for SELECT
                # or SAVEPOINT. Establish a real outer transaction so released
                # savepoints cannot escape a later batch rollback.
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            current = database_now(session)
            rows = session.scalars(
                select(Schedule)
                .where(
                    Schedule.state == "active",
                    Schedule.next_due <= current,
                    *((Schedule.workspace == workspace,) if workspace is not None else ()),
                )
                .order_by(Schedule.next_due, Schedule.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()

            def pause_schedule(schedule, expected_due, reason):
                updated_id = session.scalar(
                    update(Schedule)
                    .where(
                        Schedule.id == schedule.id,
                        Schedule.state == "active",
                        Schedule.next_due == expected_due,
                    )
                    .values(state="paused", version=Schedule.version + 1)
                    .returning(Schedule.id)
                )
                if updated_id is not None:
                    audit(
                        session,
                        schedule.workspace,
                        "scheduler",
                        "schedule.paused",
                        schedule.id,
                        {"reason": reason},
                    )

            for schedule in rows:
                previous_due = schedule.next_due
                active = session.get(
                    Resource, (schedule.workspace, "active_recipe", schedule.source_id)
                )
                if active is None or active.data.get("recipe_id") != schedule.recipe_id:
                    pause_schedule(schedule, previous_due, "RECIPE_NOT_ACTIVE")
                    continue
                run_id = uid()
                try:
                    with session.begin_nested():
                        updated_id = session.scalar(
                            update(Schedule)
                            .where(
                                Schedule.id == schedule.id,
                                Schedule.state == "active",
                                Schedule.next_due == previous_due,
                            )
                            .values(
                                next_due=current + timedelta(seconds=schedule.interval_seconds),
                                last_run_id=run_id,
                                version=Schedule.version + 1,
                            )
                            .returning(Schedule.id)
                        )
                        if updated_id is None:
                            continue
                        # Claim admission must happen BEFORE inserting run/job
                        # rows, not after unbounded schedules fill the queue.
                        flags = (admission(session, schedule) or {}) if admission else {}
                        session.add(
                            Resource(
                                workspace=schedule.workspace,
                                kind="run",
                                id=run_id,
                                data={
                                    "source_id": schedule.source_id,
                                    "recipe_id": schedule.recipe_id,
                                    "snapshot_hash": None,
                                    "state": "queued",
                                    "classification": "NOT_CONFIGURED",
                                    "schedule_id": schedule.id,
                                },
                            )
                        )
                        job_id = uid()
                        session.add(
                            Job(
                                id=job_id,
                                workspace=schedule.workspace,
                                kind="run",
                                payload={**flags, "run_id": run_id},
                                available=current,
                                created=current,
                            )
                        )
                        audit(
                            session,
                            schedule.workspace,
                            "scheduler",
                            "schedule.dispatched",
                            schedule.id,
                            {"run_id": run_id, "operation_id": job_id},
                        )
                except PipelineError as exc:
                    if exc.code not in {"QUOTA_EXCEEDED", "SOURCE_DENIED", "LIMIT_REACHED"}:
                        raise
                    # All admission writes have rolled back. Persist one pause
                    # outside that savepoint; inactive schedules are not retried
                    # on every tick. Explicit API resume preserves ETag fencing.
                    pause_schedule(schedule, previous_due, exc.code)
                    continue
                dispatched += 1
        return dispatched


def fence(claim: dict, current: datetime):
    return (
        Job.id == claim["id"],
        Job.workspace == claim["workspace"],
        Job.owner == claim["owner"],
        Job.generation == claim["generation"],
        Job.state == "running",
        Job.lease_until > current,
    )


def current_job(session, claim: dict) -> Job:
    job = session.scalar(select(Job).where(*fence(claim, database_now(session))).with_for_update())
    if job is None:
        raise PipelineError("LEASE_LOST", "worker no longer owns this generation")
    return job


def get_resource(session, workspace: str, kind: str, key: str) -> Resource:
    row = session.get(Resource, (workspace, kind, key))
    if row is None:
        raise PipelineError("NOT_FOUND", f"{kind} resource not found")
    return row


def audit(
    session, workspace: str, actor: str, action: str, target: str, details: dict | None = None
):
    event = {"actor": actor, "action": action, "target": target, "details": details or {}}
    session.add(Audit(workspace=workspace, **event))
    session.add(Outbox(workspace=workspace, event=event))


def idempotent(session, workspace: str, key: str, payload: dict, produce):
    request_hash = object_hash(payload)
    existing = session.get(Idempotency, (workspace, key))
    if existing:
        if existing.request_hash != request_hash:
            raise PipelineError(
                "IDEMPOTENCY_CONFLICT", "key was already used for a different request"
            )
        return existing.response
    result = produce()
    session.add(
        Idempotency(workspace=workspace, key=key, request_hash=request_hash, response=result)
    )
    return result
