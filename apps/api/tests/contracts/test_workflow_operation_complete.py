from uuid import UUID

from app.api.routes.workflow_operation_complete import (
    INSERT_CHAIN_SQL,
    INSERT_OPERATION_SQL,
    RESERVATION_SQL,
    WorkflowOperationCompleteRequest,
    WorkflowOperationCompleteResponse,
)
from app.main import AppDependencies, create_app
from fastapi.testclient import TestClient
from pydantic import TypeAdapter


def _request() -> WorkflowOperationCompleteRequest:
    return WorkflowOperationCompleteRequest(
        repository="63amg0010-cpu/prediction-market-monitor",
        workflow="ci.yml",
        display_title="ci-22222222-2222-4222-8222-222222222222-attempt-1",
        head_sha="a" * 40,
        approved_plan_sha256="b" * 64,
        activation_nonce=UUID("11111111-1111-4111-8111-111111111111"),
        dispatch_nonce=UUID("22222222-2222-4222-8222-222222222222"),
        reservation_sha256="c" * 64,
        run_id=123,
        run_attempt=1,
        event="workflow_dispatch",
        ref="refs/heads/main",
        environment=None,
        command="ci",
        evidence_sha256="d" * 64,
        outcome="success",
    )


def test_workflow_operation_route_is_schema_closed_and_registered() -> None:
    with TestClient(create_app(AppDependencies())) as client:
        response = client.post(
            "/internal/release/workflow-operation-complete",
            headers={"Authorization": "Bearer invalid"},
            json={**_request().model_dump(mode="json"), "database_url": "forbidden"},
        )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_workflow_operation_response_is_schema_closed() -> None:
    schema = WorkflowOperationCompleteResponse.model_json_schema()
    required = TypeAdapter(list[str]).validate_python(schema["required"])

    assert schema["additionalProperties"] is False
    assert set(required) >= {
        "command",
        "reviewed_sha",
        "reservation_receipt_sha256",
        "artifact_sha256",
        "committed_revision",
    }


def test_workflow_operation_sql_binds_claim_and_appends_terminal_receipt() -> None:
    for field in (
        "reservation.claimed_run_id = :run_id",
        "reservation.claimed_run_attempt = :run_attempt",
        "reservation.display_title = :display_title",
        "reservation.workflow_file = :workflow",
        "reservation.head_sha = :head_sha",
    ):
        assert field in RESERVATION_SQL
    assert "INSERT INTO release_operation_receipts" in INSERT_OPERATION_SQL
    assert "true, true, false" in INSERT_OPERATION_SQL
    assert "INSERT INTO release_receipt_chain" in INSERT_CHAIN_SQL
    assert ":reservation_sha256" in INSERT_CHAIN_SQL
