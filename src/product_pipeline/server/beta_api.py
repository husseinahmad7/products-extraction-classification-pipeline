"""Public-beta approval and immutable learning workflow. Models cannot auto-promote."""

from typing import Literal

from fastapi import Depends, Header, Query
from fastapi.responses import JSONResponse, Response
from pydantic import Field, field_validator
from sqlalchemy import select

from product_pipeline.contracts import Contract, ExtractionResult, TaxonomySpec
from product_pipeline.errors import PipelineError
from product_pipeline.hashing import canonical, object_hash

from .db import ApprovedDomain, BudgetUsage, DomainRequest, Resource, audit, get_resource, now, uid
from .identity import identity, operator, workspace
from .public_beta import GLOBAL, limits, normalize_domain, reserve


class DomainSubmission(Contract):
    domain: str = Field(min_length=3, max_length=253)
    reason: str = Field(min_length=5, max_length=2000)

    @field_validator("domain")
    @classmethod
    def domain_name(cls, value):
        return normalize_domain(value)


class DomainDecision(Contract):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=5, max_length=2000)


class LabelCorrection(Contract):
    taxonomy_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)
    record_id: str = Field(min_length=1, max_length=128)
    label_id: str | None = Field(default=None, max_length=128)
    provenance: Literal["human", "ai"]
    reason: str = Field(min_length=5, max_length=2000)
    supersedes: str | None = Field(default=None, max_length=128)


class DatasetSnapshot(Contract):
    taxonomy_id: str = Field(min_length=1, max_length=128)
    provenance: Literal["human", "ai", "all"] = "human"
    label_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)


class ModelRegistration(Contract):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    provider: Literal["tfidf", "setfit", "gliclass", "laya"]
    taxonomy_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    checkpoint_revision: str = Field(min_length=1, max_length=128)
    artifact_uri: str = Field(min_length=1, max_length=2000)
    artifact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    calibration_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    license: str = Field(min_length=1, max_length=128)

    @field_validator("artifact_uri")
    @classmethod
    def remote_only(cls, value):
        from urllib.parse import urlsplit

        from product_pipeline.contracts import SourceSpec

        SourceSpec.valid_url(value)
        if urlsplit(value).scheme != "https":
            raise ValueError("model artifacts must stay in a remote HTTPS registry")
        return value


def resource_view(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "version": row.version,
        "created": row.created.isoformat() if row.created else None,
        "data": row.data,
    }


def domain_view(row):
    return {
        "id": row.id,
        "kind": "domain_request",
        "version": row.version,
        "created": row.created.isoformat(),
        "data": {
            "domain": row.domain,
            "state": row.state,
            "reason": row.reason,
            "workspace": row.workspace,
            "decision_reason": row.decision_reason,
            "decided_by": row.decided_by,
        },
    }


def register_beta_routes(app, db, settings, artifacts, actor, mutate, expect_version):
    @app.get("/v1/me")
    def me(_who=Depends(actor)):
        who = identity()
        return {
            "actor": who.actor,
            "workspace": who.workspace,
            "is_operator": who.is_operator,
            "auth_mode": who.auth_mode,
        }

    @app.get("/v1/budget")
    def budget(_who=Depends(actor)):
        period = now().strftime("%Y-%m")
        with db.sessions() as session:

            def report(scope):
                row = session.get(BudgetUsage, (scope, period))
                lifetime = session.get(BudgetUsage, (scope, "total"))
                return {
                    "period": period,
                    "metadata_period": "lifetime",
                    "reserved": {
                        **(row.counters if row else {}),
                        **(lifetime.counters if lifetime else {}),
                    },
                    "limits": limits(settings, scope == GLOBAL),
                }

            return {
                "mode": settings.auth_mode,
                "workspace": report(workspace()),
                "global": report(GLOBAL),
                "accounting": "conservative_reservations_not_provider_billing",
            }

    @app.get("/v1/approved-domains")
    def approved(_who=Depends(actor)):
        with db.sessions() as session:
            rows = session.scalars(
                select(ApprovedDomain)
                .where(ApprovedDomain.enabled.is_(True))
                .order_by(ApprovedDomain.domain)
            ).all()
            return {"items": [{"domain": row.domain} for row in rows], "next_cursor": None}

    @app.post("/v1/domain-requests", status_code=201)
    def submit_domain(
        body: DomainSubmission,
        who=Depends(actor),
        idempotency_key: str | None = Header(default=None),
    ):
        def create(session):
            row = DomainRequest(
                id=uid(), workspace=workspace(), domain=body.domain, reason=body.reason
            )
            session.add(row)
            session.flush()
            audit(session, workspace(), who, "domain.requested", row.id, {"domain": row.domain})
            return domain_view(row)

        return mutate(idempotency_key, "domain.request", body.model_dump(), create)

    @app.get("/v1/domain-requests")
    def list_domains(
        cursor: str = "", limit: int = Query(default=50, ge=1, le=100), _who=Depends(actor)
    ):
        with db.sessions() as session:
            query = select(DomainRequest).where(DomainRequest.id > cursor)
            if not identity().is_operator:
                query = query.where(DomainRequest.workspace == workspace())
            rows = session.scalars(query.order_by(DomainRequest.id).limit(limit + 1)).all()
            return {
                "items": [domain_view(row) for row in rows[:limit]],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    @app.get("/v1/domain-requests/{request_id}")
    def get_domain(request_id: str, _who=Depends(actor)):
        with db.sessions() as session:
            row = session.get(DomainRequest, request_id)
            if row is None or (not identity().is_operator and row.workspace != workspace()):
                raise PipelineError("NOT_FOUND", "domain request not found")
            return JSONResponse(domain_view(row), headers={"ETag": f'"{row.version}"'})

    @app.post("/v1/domain-requests/{request_id}/decision")
    def decide_domain(
        request_id: str,
        body: DomainDecision,
        who=Depends(actor),
        if_match: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        operator()

        def decide(session):
            row = session.scalar(
                select(DomainRequest).where(DomainRequest.id == request_id).with_for_update()
            )
            if row is None:
                raise PipelineError("NOT_FOUND", "domain request not found")
            expect_version(row, if_match)
            if row.state != "pending":
                raise PipelineError("CONFLICT", "domain request already decided")
            row.state = "approved" if body.decision == "approve" else "rejected"
            row.decision_reason, row.decided_by, row.version = body.reason, who, row.version + 1
            if body.decision == "approve":
                approval = session.get(ApprovedDomain, row.domain)
                if approval:
                    approval.enabled, approval.actor, approval.reason = True, who, body.reason
                else:
                    session.add(
                        ApprovedDomain(
                            domain=row.domain, enabled=True, actor=who, reason=body.reason
                        )
                    )
            audit(
                session,
                row.workspace,
                who,
                "domain." + row.state,
                row.id,
                {"domain": row.domain, "reason": body.reason},
            )
            return domain_view(row)

        return mutate(
            idempotency_key,
            "domain.decision:" + request_id,
            {**body.model_dump(), "if_match": if_match},
            decide,
        )

    @app.post("/v1/approved-domains/{domain}/revoke")
    def revoke_domain(
        domain: str,
        body: DomainDecision,
        who=Depends(actor),
        idempotency_key: str | None = Header(default=None),
    ):
        operator()

        def revoke(session):
            row = session.get(ApprovedDomain, normalize_domain(domain))
            if row is None:
                raise PipelineError("NOT_FOUND", "approved domain not found")
            row.enabled, row.actor, row.reason = False, who, body.reason
            audit(session, workspace(), who, "domain.revoked", domain, {"reason": body.reason})
            return {"domain": row.domain, "enabled": False}

        return mutate(idempotency_key, "domain.revoke:" + domain, body.model_dump(), revoke)

    @app.post("/v1/labels", status_code=201)
    def correct_label(
        body: LabelCorrection,
        who=Depends(actor),
        idempotency_key: str | None = Header(default=None),
    ):
        def create(session):
            taxonomy = TaxonomySpec.model_validate(
                get_resource(session, workspace(), "taxonomy", body.taxonomy_id).data
            )
            if body.label_id is not None and body.label_id not in {
                node.id for node in taxonomy.nodes
            }:
                raise PipelineError("LABEL_INVALID", "label is not in this taxonomy")
            revision = get_resource(session, workspace(), "revision", body.revision_id)
            result = ExtractionResult.model_validate_json(
                artifacts.get(revision.data["result_key"])
            )
            record = next(
                (record for record in result.records if record.id == body.record_id), None
            )
            if record is None:
                raise PipelineError("NOT_FOUND", "record not found in this retained revision")
            if body.supersedes:
                previous = get_resource(session, workspace(), "label", body.supersedes)
                if any(
                    previous.data[key] != value
                    for key, value in {
                        "record_id": body.record_id,
                        "taxonomy_id": body.taxonomy_id,
                        "revision_id": body.revision_id,
                    }.items()
                ):
                    raise PipelineError(
                        "LABEL_INVALID",
                        "superseded label must refer to the same retained record and taxonomy",
                    )
            label_id = uid()
            value = {
                **body.model_dump(),
                "actor": who,
                "taxonomy_hash": object_hash(taxonomy.model_dump(mode="json")),
                "result_hash": result.result_hash,
                "record_hash": object_hash(record.model_dump(mode="json")),
                "source_id": record.source_id,
                "source_group": record.source_id,
                "result_key": revision.data["result_key"],
                "provenance_assurance": "reviewer_declared_not_independently_certified",
            }
            row = Resource(workspace=workspace(), kind="label", id=label_id, data=value)
            session.add(row)
            session.flush()
            audit(
                session,
                workspace(),
                who,
                "label.corrected",
                label_id,
                {
                    "record_id": body.record_id,
                    "provenance": body.provenance,
                    "supersedes": body.supersedes,
                },
            )
            return resource_view(row)

        return mutate(idempotency_key, "label.correct", body.model_dump(), create)

    @app.post("/v1/datasets/snapshots", status_code=201)
    def snapshot(
        body: DatasetSnapshot,
        who=Depends(actor),
        idempotency_key: str | None = Header(default=None),
    ):
        def create(session):
            taxonomy = get_resource(session, workspace(), "taxonomy", body.taxonomy_id)
            taxonomy_hash = object_hash(taxonomy.data)
            labels = session.scalars(
                select(Resource)
                .where(Resource.workspace == workspace(), Resource.kind == "label")
                .order_by(Resource.created, Resource.id)
            ).yield_per(100)
            latest = {}
            for row in labels:
                value = row.data
                if (
                    value["taxonomy_id"] == body.taxonomy_id
                    and value["taxonomy_hash"] == taxonomy_hash
                ):
                    latest[(value["record_id"], value["record_hash"])] = row
            selected = [
                row
                for row in latest.values()
                if body.provenance == "all" or row.data["provenance"] == body.provenance
            ]
            if not selected:
                raise PipelineError(
                    "DATASET_EMPTY", "no matching reviewed labels; create corrections first"
                )
            selected.sort(key=lambda row: row.id)
            if body.label_ids is not None:
                requested = set(body.label_ids)
                if len(requested) != len(body.label_ids) or not requested.issubset(
                    {row.id for row in selected}
                ):
                    raise PipelineError(
                        "DATASET_LABEL_INVALID",
                        "select distinct latest corrections from this workspace, taxonomy and provenance",
                    )
                selected = [row for row in selected if row.id in requested]
            if len(selected) > 1000:
                raise PipelineError(
                    "LIMIT_REACHED", "split training snapshots into at most 1000 reviewed records"
                )
            retained = {}
            exported_rows = []
            for correction in selected:
                value = correction.data
                if value["result_key"] not in retained:
                    result = ExtractionResult.model_validate_json(
                        artifacts.get(value["result_key"])
                    )
                    retained[value["result_key"]] = (
                        result.result_hash,
                        {record.id: record for record in result.records},
                    )
                result_hash, records = retained[value["result_key"]]
                record = records.get(value["record_id"])
                if (
                    result_hash != value["result_hash"]
                    or record is None
                    or object_hash(record.model_dump(mode="json")) != value["record_hash"]
                ):
                    raise PipelineError(
                        "ARTIFACT_CORRUPT",
                        "reviewed record reference does not match retained evidence",
                    )
                exported_rows.append(
                    {
                        "correction_id": correction.id,
                        **value,
                        "record": record.model_dump(mode="json"),
                    }
                )
            export = {
                "schema_version": "training-dataset/v1",
                "taxonomy": taxonomy.data,
                "taxonomy_hash": taxonomy_hash,
                "rows": exported_rows,
                "certification_eligible": False,
                "auto_accept_enabled": False,
                "split_policy": "group_by_source_and_duplicate_record_before_training",
            }
            export_key = artifacts.put(canonical(export))
            dataset_id = uid()
            value = {
                "taxonomy_id": body.taxonomy_id,
                "taxonomy_hash": taxonomy_hash,
                "dataset_hash": object_hash(export),
                "artifact_hash": export_key,
                "label_ids": [row.id for row in selected],
                "records": len(selected),
                "provenance": body.provenance,
                "certification_eligible": False,
                "auto_accept_enabled": False,
            }
            if settings.auth_mode == "neon":
                reserve(
                    session, workspace(), {"metadata_bytes": 3 * len(canonical(value))}, settings
                )
            row = Resource(workspace=workspace(), kind="dataset", id=dataset_id, data=value)
            session.add(row)
            session.flush()
            audit(
                session,
                workspace(),
                who,
                "dataset.snapshot",
                dataset_id,
                {"dataset_hash": value["dataset_hash"], "provenance": body.provenance},
            )
            return resource_view(row)

        return mutate(idempotency_key, "dataset.snapshot", body.model_dump(), create)

    @app.get("/v1/datasets/{dataset_id}/export")
    def export(dataset_id: str, _who=Depends(actor)):
        with db.sessions() as session:
            row = get_resource(session, workspace(), "dataset", dataset_id)
            data = artifacts.get(row.data["artifact_hash"])
        return Response(
            data,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="training-{row.id}.json"'},
        )

    @app.post("/v1/models", status_code=201)
    def register_model(
        body: ModelRegistration,
        who=Depends(actor),
        idempotency_key: str | None = Header(default=None),
    ):
        def create(session):
            dataset = get_resource(session, workspace(), "dataset", body.dataset_id)
            taxonomy = get_resource(session, workspace(), "taxonomy", body.taxonomy_id)
            if dataset.data["taxonomy_hash"] != object_hash(taxonomy.data):
                raise PipelineError(
                    "MODEL_BINDING_INVALID", "dataset taxonomy differs from current taxonomy"
                )
            value = {
                **body.model_dump(),
                "dataset_hash": dataset.data["dataset_hash"],
                "taxonomy_hash": dataset.data["taxonomy_hash"],
                "state": "registered_not_certified",
                "auto_accept_enabled": False,
                "automatic_promotion": False,
            }
            row = Resource(workspace=workspace(), kind="model", id=body.id, data=value)
            session.add(row)
            session.flush()
            audit(
                session,
                workspace(),
                who,
                "model.registered",
                row.id,
                {"artifact_hash": body.artifact_hash, "state": value["state"]},
            )
            return resource_view(row)

        return mutate(idempotency_key, "model.register", body.model_dump(), create)
