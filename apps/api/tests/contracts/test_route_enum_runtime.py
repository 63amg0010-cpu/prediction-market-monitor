from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

import httpx2
import pytest
from app.analysis.output import AnalysisOutput
from app.api.routes.health import create_health_router
from app.api.routes.verification import VerificationSourceResult
from app.api.routes.worker import AckKind, WorkerAckPayload
from app.domain.enums import Sentiment, VerificationStatus
from app.services.dashboard.models import DatabaseStatus
from fastapi import FastAPI


@dataclass(frozen=True, slots=True)
class _DatabaseProbe:
    status: DatabaseStatus

    async def database_status(self) -> DatabaseStatus:
        return self.status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_status", "expected_status"),
    [
        (DatabaseStatus.OK, "ok"),
        (DatabaseStatus.UNAVAILABLE, "degraded"),
    ],
)
async def test_health_projects_every_database_status_at_runtime(
    database_status: DatabaseStatus,
    expected_status: Literal["ok", "degraded"],
) -> None:
    # Given
    app = FastAPI()
    app.include_router(
        create_health_router(_DatabaseProbe(database_status), version="1")
    )

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        response = await client.get("/v1/health")

    # Then
    assert response.status_code == 200
    assert response.json() == {
        "status": expected_status,
        "version": "1",
        "db": database_status,
    }


@pytest.mark.parametrize(
    ("verification_status", "failure_code"),
    [
        (VerificationStatus.PASSED, None),
        (VerificationStatus.FAILED, "source_failed"),
        (VerificationStatus.MISSING, "source_missing"),
    ],
)
def test_verification_accepts_every_valid_status_at_runtime(
    verification_status: VerificationStatus,
    failure_code: str | None,
) -> None:
    # Given
    passed = verification_status is VerificationStatus.PASSED
    collection_recency = 60 if passed else None
    publication_latency = 120 if passed else None

    # When
    result = VerificationSourceResult(
        source_id=uuid4(),
        scheduler_latency_seconds=30,
        collection_recency_seconds=collection_recency,
        publication_latency_seconds=publication_latency,
        status=verification_status,
        failure_code=failure_code,
    )

    # Then
    assert result.status is verification_status


@pytest.mark.parametrize("kind", [AckKind.SUCCESS, AckKind.RETRYABLE_FAILURE])
def test_worker_accepts_every_valid_ack_kind_at_runtime(kind: AckKind) -> None:
    # Given
    success = kind is AckKind.SUCCESS

    # When
    payload = WorkerAckPayload(
        kind=kind,
        item_id=uuid4(),
        post_version_id=uuid4(),
        content_hash="a" * 64,
        prompt_version="prompt-v1",
        model_version="model-v1",
        schema_version="schema-v1",
        lease_token="l" * 43,
        output=(
            AnalysisOutput(
                relevance=True,
                sentiment=Sentiment.NEUTRAL,
                topics=("rates",),
            )
            if success
            else None
        ),
        error_code=None if success else "retryable",
    )

    # Then
    assert payload.kind is kind
    assert payload.output is not None if success else payload.error_code == "retryable"
