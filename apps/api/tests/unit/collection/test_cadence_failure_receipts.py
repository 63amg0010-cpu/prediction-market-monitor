"""Failing-operation cadence receipts stay public-safe and retry-exact."""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Self, cast, override
from uuid import UUID

import anyio
import httpx2
import pytest
from app.collection import collector_command, verification_command
from app.collection.cadence_result import (
    CadenceOperationResult,
    failure_receipt_hash,
)
from app.services.release.cadence_workflow_models import (
    CadenceWorkflowAttemptRequest,
    SourceResult,
)
from app.services.release.cadence_workflow_validation import retry_permitted
from pydantic import ValidationError
from scripts import release_cadence_workflow_client

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from types import TracebackType

SOURCE_A = UUID("11111111-1111-4111-8111-111111111111")
SOURCE_B = UUID("22222222-2222-4222-8222-222222222222")
EPOCH = UUID("33333333-3333-4333-8333-333333333333")
SLOT = "2026-07-30T03:17:00Z"
DUE = datetime(2026, 7, 30, 3, 17, tzinfo=UTC)


class _Client(AbstractAsyncContextManager["_Client"]):
    @override
    async def __aenter__(self) -> Self:
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def exchange_github_oidc(self) -> str:
        raise TimeoutError


def _environment(path: Path) -> dict[str, str]:
    return {
        "MONITOR_API_URL": "https://control.invalid",
        "MONITOR_SCOPE_VERSION": "scope-v1",
        "MONITOR_DEPLOYMENT_ACTIVATION_AT": "2026-07-30T00:00:00Z",
        "MONITOR_SOURCE_IDS": f"{SOURCE_A},{SOURCE_B}",
        "MONITOR_CADENCE_SLOT_KEY": SLOT,
        "MONITOR_CADENCE_RESULT_PATH": str(path),
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
    }


def _assert_public_failure(path: Path, kind: str) -> None:
    raw = path.read_text(encoding="utf-8")
    result = CadenceOperationResult.model_validate_json(raw)
    assert result.schedule_kind == kind
    assert len(result.source_results) == 2
    assert {item.source_id for item in result.source_results} == {
        SOURCE_A,
        SOURCE_B,
    }
    assert {
        (item.status, item.code, item.retry_classification)
        for item in result.source_results
    } == {("failed", "transient_timeout", "safe_terminal")}
    loaded = cast("dict[str, object]", json.loads(raw))
    assert set(loaded) == {
        "completed_at",
        "schedule_kind",
        "schema",
        "slot_key",
        "source_results",
        "started_at",
    }
    for forbidden in ("TimeoutError", "control.invalid", "GITHUB", "traceback"):
        assert forbidden not in raw


@pytest.mark.asyncio
async def test_collect_writes_two_source_failure_before_reraising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "result.json"

    async def sources(*_args: object) -> tuple[()]:
        return ()

    async def fail(*_args: object) -> None:
        message = "provider response content must not escape"
        raise TimeoutError(message)

    def client_factory(
        _base_url: str, _environment: Mapping[str, str]
    ) -> _Client:
        return _Client()

    monkeypatch.setattr(collector_command, "ControlPlaneClient", client_factory)
    monkeypatch.setattr(collector_command, "source_executions", sources)
    monkeypatch.setattr(collector_command, "execute_collect_command", fail)

    with pytest.raises(TimeoutError):
        await collector_command.collect(_environment(output))

    _assert_public_failure(output, "collection")


@pytest.mark.asyncio
async def test_verify_writes_two_source_failure_before_reraising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "result.json"

    def client_factory(
        _base_url: str, _environment: Mapping[str, str]
    ) -> _Client:
        return _Client()

    monkeypatch.setattr(
        verification_command, "ControlPlaneClient", client_factory
    )

    with pytest.raises(TimeoutError):
        await verification_command.verify(_environment(output))

    _assert_public_failure(output, "verifier")


def _request(
    *,
    code: Literal[
        "transient_timeout",
        "transient_transport",
        "operation_rejected",
        "unexpected_failure",
    ],
    classification: Literal["safe_terminal", "hold"],
    attempt: int = 1,
) -> CadenceWorkflowAttemptRequest:
    return CadenceWorkflowAttemptRequest(
        repository="owner/repository",
        workflow="collect.yml",
        head_sha="a" * 40,
        ref="refs/heads/main",
        event="schedule" if attempt == 1 else "workflow_dispatch",
        environment="production-collector",
        run_id=100,
        run_attempt=1,
        epoch_id=EPOCH,
        schedule_kind="collection",
        slot_key=SLOT,
        workflow_mode="schedule" if attempt == 1 else "retry",
        cadence_attempt=attempt,
        failed_predecessor_attempt_id=None if attempt == 1 else EPOCH,
        started_at=DUE + timedelta(minutes=1),
        completed_at=DUE + timedelta(minutes=2),
        source_results=tuple(
            SourceResult(
                source_id=source,
                status="failed",
                code=code,
                retry_classification=classification,
                receipt_sha256=failure_receipt_hash(source, code),
            )
            for source in (SOURCE_A, SOURCE_B)
        ),
    )


def test_only_explicit_safe_terminal_attempt_one_can_authorize_retry() -> None:
    row: Mapping[str, object] = {"due_at": DUE}
    safe = _request(code="transient_timeout", classification="safe_terminal")
    hold = _request(code="unexpected_failure", classification="hold")
    retry_two = _request(
        code="transient_timeout", classification="safe_terminal", attempt=2
    )

    assert retry_permitted(safe, row, "source_failed") is True
    assert retry_permitted(hold, row, "source_failed") is False
    assert retry_permitted(retry_two, row, "source_failed") is False
    assert retry_permitted(safe, row, "source_set_mismatch") is False


def test_failure_shape_is_schema_closed() -> None:
    payload = {
        "source_id": str(SOURCE_A),
        "status": "failed",
        "code": "transient_timeout",
        "retry_classification": "safe_terminal",
        "receipt_sha256": "d" * 64,
        "exception": "secret",
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _ = SourceResult.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, b"{not-json"])
async def test_missing_or_corrupt_result_fails_before_identity_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: bytes | None,
) -> None:
    result_path = tmp_path / "operation-result.json"
    if content is not None:
        _ = result_path.write_bytes(content)
    args = release_cadence_workflow_client.Arguments()
    args.api_url = "https://must-not-run.invalid"
    args.epoch_id = str(EPOCH)
    args.mode = "schedule"
    args.cadence_attempt = 1
    args.failed_predecessor_attempt_id = "none"
    args.result = str(result_path)
    args.json_out = str(tmp_path / "receipt.json")
    network_calls = 0

    def unexpected_client(*_args: object, **_kwargs: object) -> None:
        nonlocal network_calls
        network_calls += 1
        message = "network_must_not_run_for_invalid_result"
        raise AssertionError(message)

    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        unexpected_client,
    )
    expected_error = FileNotFoundError if content is None else ValidationError
    with pytest.raises(expected_error):
        _ = await release_cadence_workflow_client.record(args, {})
    assert network_calls == 0
    assert not await anyio.Path(args.json_out).exists()
