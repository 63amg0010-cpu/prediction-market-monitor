from __future__ import annotations

# ruff: noqa: E402, EM101, TRY003
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# pyright: reportImplicitStringConcatenation=false
# pyright: reportPrivateUsage=false, reportUnusedCallResult=false
# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import release_runtime_compat_handler as handlers
from scripts.release_vercel_models import (
    TEAM_SLUG,
    ReleaseHoldError,
    seal_receipt,
)
from scripts.release_vercel_retention import (
    EVIDENCE_SOURCE,
    AliasRetentionObservation,
)

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
ACTIVATION = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SHA = "a" * 40
PLAN = "b" * 64


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (None, "cadence_anchor_at_missing"),
        ("not-a-timestamp", "cadence_anchor_at_invalid"),
        ("2026-07-01T00:00:00", "cadence_anchor_at_must_be_utc_aware"),
        (
            "2026-07-29T12:00:00+09:00",
            "cadence_anchor_at_must_be_utc_aware",
        ),
        (
            (NOW - timedelta(days=31)).isoformat(),
            "alias_retention_expired",
        ),
    ],
)
def test_compat_anchor_holds_before_runtime_io(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    code: str,
) -> None:
    calls: list[str] = []

    def fail_if_called(_args: object, _anchor: datetime) -> object:
        calls.append("io")
        raise AssertionError("runtime I/O must not start")

    monkeypatch.setattr(handlers, "_load_states", fail_if_called)
    args = SimpleNamespace(cadence_anchor_at=value)

    with pytest.raises(ReleaseHoldError, match=rf"^{code}$"):
        handlers.compat_state(args, now=lambda: NOW)

    assert calls == []


def _receipt(**values: object) -> dict[str, object]:
    return seal_receipt(
        {
            "reviewed_sha": SHA,
            "approved_plan_sha256": PLAN,
            "activation_nonce": str(ACTIVATION),
            "accepted": True,
            **values,
        }
    )


class ObservationRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime]] = []

    def observe(
        self,
        kind: str,
        receipt: dict[str, object],
        observed_at: datetime,
    ) -> AliasRetentionObservation:
        self.calls.append((kind, observed_at))
        return AliasRetentionObservation(
            project_kind=kind,  # type: ignore[arg-type]
            alias=str(receipt["alias"]),
            deployment_id=str(receipt["deployment_id"]),
            project_name=str(receipt["project_name"]),
            team_slug=str(receipt["team_slug"]),
            source_sha=str(receipt["source_sha"]),
            ready_state="READY",
            environment="production",
            evidence_source=EVIDENCE_SOURCE,
            observed_at=observed_at,
        )


@pytest.mark.parametrize("elapsed", [timedelta(0), timedelta(days=30)])
def test_compat_passes_exact_db_time_current_and_renewal_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    elapsed: timedelta,
) -> None:
    anchor = NOW
    db_now = anchor + elapsed
    database = SimpleNamespace(
        revision="20260727_0010",
        manifold_enabled=False,
        active_authorization_id=None,
        current_budget_id=None,
        current_binding_id=None,
        current_cadence_id=None,
    )
    monkeypatch.setattr(
        handlers,
        "_load_states",
        lambda _args, _anchor: SimpleNamespace(
            state=database,
            observed_at=db_now,
        ),
    )
    api_alias = _receipt(
        alias="prediction-monitor-api-fresh-search-compat.vercel.app",
        deployment_id="dpl_api",
        project_name="prediction-monitor-api",
        team_slug=TEAM_SLUG,
        source_sha=SHA,
    )
    web_alias = _receipt(
        alias="prediction-monitor-web-fresh-search-compat.vercel.app",
        deployment_id="dpl_web",
        project_name="prediction-monitor-web",
        team_slug=TEAM_SLUG,
        source_sha=SHA,
    )
    documents = {
        "api": _receipt(),
        "web": _receipt(),
        "api-alias": api_alias,
        "web-alias": web_alias,
        "predecessor": _receipt(),
    }
    monkeypatch.setattr(handlers, "read_document", documents.__getitem__)
    monkeypatch.setattr(handlers, "deployment_state", lambda *_a, **_k: object())
    monkeypatch.setattr(
        handlers,
        "health_state",
        lambda *_a, **_k: (
            object(),
            {
                "manifold_rows": 0,
                "claim_endpoint_compatible": True,
                "evidence_endpoint_compatible": True,
            },
        ),
    )
    captured: list[object] = []
    monkeypatch.setattr(
        handlers,
        "validate_compat_state",
        lambda request: captured.append(request) or {"accepted": True},
    )
    monkeypatch.setattr(handlers, "write_document", lambda *_args: None)
    runtime = ObservationRuntime()
    args = SimpleNamespace(
        cadence_anchor_at=anchor.isoformat(),
        api_alias_receipt="api-alias",
        web_alias_receipt="web-alias",
        api_receipt="api",
        web_receipt="web",
        predecessor_receipt="predecessor",
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=str(ACTIVATION),
        api_url="https://api.example.test",
        web_url="https://web.example.test",
        json_out="out",
    )

    assert (
        handlers.compat_state(
            args,
            now=lambda: db_now,
            retention_runtime=runtime,
        )
        == 0
    )
    request = captured[0]
    assert request.cadence_anchor_at == anchor
    assert request.db_now == db_now
    assert request.api_retention.rechecked_at == db_now
    assert request.web_retention.rechecked_at == db_now
    assert request.api_retention.renewal_recheck_at == anchor + timedelta(
        days=30
    )
    assert runtime.calls == [("api", db_now), ("web", db_now)]


def test_db_expiry_boundary_holds_before_provider_or_http_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = NOW - timedelta(days=31)
    calls: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            calls.append("dispose")

    async def snapshot(_engine: object, _nonce: UUID) -> object:
        calls.append("database")
        return SimpleNamespace(state=object(), observed_at=NOW)

    monkeypatch.setattr(handlers, "engine_from_named_env", lambda _name: Engine())
    monkeypatch.setattr(handlers, "rollback_database_snapshot", snapshot)
    monkeypatch.setattr(
        handlers,
        "health_state",
        lambda *_a, **_k: calls.append("http"),
    )
    args = SimpleNamespace(
        database_url_env="DATABASE_URL",
        activation_nonce=str(ACTIVATION),
    )

    with pytest.raises(ReleaseHoldError, match=r"^alias_retention_expired$"):
        handlers._load_states(args, anchor)

    assert calls == ["database", "dispose"]
